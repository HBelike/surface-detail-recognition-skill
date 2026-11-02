"""观察→必要放大→至多一次冲突裁决；所有阶段保留，达到边界后拒判。"""
import copy
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from domain import Inspection, Verification
from evidence_resolution import Claim, resolve_pair
from execution import execution_scope
from imaging import discover, map_box
from inspect_images import atomic_json, item_path, render_report, validate_completed
from vision_client import analyze, PROMPT
from zoom_tools import evidence_id, overlap_fraction, plan_zoom

CATEGORIES = ('linear_trace', 'material_loss')


def observation_id(stage, observation):
    return 'o-' + hashlib.sha256(json.dumps([stage, observation], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]


def register_result(result, entries, stage):
    lookup = {e['image_id']: e for e in entries}; records = []
    for finding in result.findings:
        source = lookup[finding.image_id]['source']; box = map_box(finding.bbox, lookup[finding.image_id])
        records.append({**finding.model_dump(), 'evidence_id': evidence_id(finding.category, source, box),
                        'source': source, 'bbox_pixels': box, 'stage': stage, 'kind': 'finding'})
    for observation in result.observations:
        data = observation.model_dump()
        records.append({**data, 'evidence_id': observation_id(stage, data), 'stage': stage, 'kind': 'observation',
                        'source': lookup[observation.image_id]['source'], 'crop_box': lookup[observation.image_id]['crop_box']})
    return records


def claim(result, ledger, category, stage):
    finding_ids = [e['evidence_id'] for e in ledger if e['stage'] == stage and e.get('category') == category]
    ids = finding_ids or [e['evidence_id'] for e in ledger if e['stage'] == stage and e['kind'] == 'observation']
    return Claim(getattr(result, category), tuple(dict.fromkeys(ids)))


def covers_prior_positive(baseline, review, entries, category):
    """同一原图的足够清晰覆盖才可能作为反证；其他可见面未见不算。"""
    lookup = {e['image_id']: e for e in entries}
    adequate = {o.image_id for o in review.inspection.observations if o.quality == 'adequate'}
    cited = set(review.assessments[category].image_ids) & adequate
    positives = [e for e in baseline['evidence'] if e['category'] == category and e['certainty'] == 'confirmed']
    return bool(positives) and all(any(lookup[i]['source'] == p['source'] and overlap_fraction(p['bbox_pixels'], lookup[i]['crop_box']) >= .9
                                         for i in cited if i in lookup) for p in positives)


def assemble(baseline, stages, decisions, flow):
    final = copy.deepcopy(baseline); metadata = {e['image_id']: e for e in baseline['input_images']}
    observations = {o['image_id']: o for o in baseline['result']['observations']}
    findings = []; seen = set(); limitations = list(baseline['result']['limitations'])
    all_results = [Inspection.model_validate(baseline['result'])]
    for stage in stages:
        metadata.update({e['image_id']: {k: v for k, v in e.items() if k != 'data_url'} for e in stage['entries']})
        all_results.append(stage['review'].inspection)
    # 状态为未发现时，旧候选仍在ledger中；最终findings只承载最终状态对应的证据。
    for stage_index, result in enumerate(all_results):
        observations.update({o.image_id: o.model_dump() for o in result.observations})
        limitations.extend(result.limitations)
        for finding in result.findings:
            data = finding.model_dump(); category = finding.category
            resolution = flow.get('resolutions', {}).get(category, {})
            if decisions[category] == 'present' and resolution.get('adjudication_evidence_complete') and stage_index != len(all_results) - 1:
                continue
            if decisions[category] == 'not_observed': continue
            if decisions[category] == 'uncertain': data['certainty'] = 'uncertain'
            identity = json.dumps(data, sort_keys=True, ensure_ascii=False)
            if identity not in seen: findings.append(data); seen.add(identity)
    # 已被裁决排除的旧confirmed不再混入最终阳性；被保留的历史记录仍可回放。
    for category in CATEGORIES:
        if decisions[category] == 'present' and not any(f['category'] == category and f['certainty'] == 'confirmed' for f in findings):
            decisions[category] = 'uncertain'
            limitations.append(category + ' 缺少可定位的确认依据，降为不确定')
    if flow.get('skipped_requests'):
        limitations.append('区域预算已达上限，部分候选未放大核验；请查看evidence_flow.skipped_evidence_ids。')
    limitations.extend(flow.get('limitations', []))
    limitations.append('同一照片及其裁剪不作为独立投票；确定性测试不证明视觉标签正确。')
    result = Inspection(**decisions, findings=findings, observations=list(observations.values()), limitations=list(dict.fromkeys(limitations)))
    final.update(result=result.model_dump(), input_images=list(metadata.values()),
                 evidence=[{**f.model_dump(), 'source': metadata[f.image_id]['source'], 'bbox_pixels': map_box(f.bbox, metadata[f.image_id])} for f in result.findings],
                 evidence_flow=flow)
    final['metadata'] = copy.deepcopy(baseline.get('metadata', {}))
    metas = [baseline.get('metadata', {})] + [s['metadata'] for s in stages]
    final['metadata']['usage'] = {k: sum(m.get('usage', {}).get(k, 0) for m in metas) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    final['metadata']['request_count'] = sum(m.get('request_count', 0) for m in metas)
    final['metadata']['requests_with_unknown_usage'] = sum(m.get('requests_with_unknown_usage', 0) for m in metas)
    final['metadata']['request_ids'] = [request for m in metas for request in m.get('request_ids', [m['request_id']] if m.get('request_id') else [])]
    final['metadata']['response_models'] = [name for m in metas for name in m.get('response_models', [m['response_model']] if m.get('response_model') else [])]
    final['refinement'] = {'status': 'completed' if stages else 'not_needed', 'policy': 'bounded_evidence',
                           'categories': list(CATEGORIES), 'baseline_result': baseline['result'], 'baseline_metadata': baseline.get('metadata', {})}
    if stages:
        final['refinement'].update(review_result=stages[0]['review'].inspection.model_dump(),
                                   review_metadata=stages[0]['metadata'],
                                   assessments={c: a.model_dump() for c, a in stages[0]['review'].assessments.items()})
    if len(stages) > 1:
        final['refinement']['adjudication'] = {'result': stages[1]['review'].model_dump(), 'metadata': stages[1]['metadata']}
    return final


def process_one(baseline, paths, output, model, base_url, timeout, thinking, force, execution=None, review_all=False):
    started = time.monotonic(); item = baseline['item_id']; dest = item_path(output, item)
    digest = hashlib.sha256(json.dumps(baseline, sort_keys=True, ensure_ascii=False).encode())
    signature = getattr(execution, 'reference_snapshot', {}).get(item, {})
    provider=getattr(execution,'provider_config',None)
    digest.update(json.dumps([model, base_url, thinking, review_all, getattr(execution, 'reasoning_effort', 'high'), getattr(execution, 'backend', 'deepseek'), signature, provider.snapshot() if provider else None], sort_keys=True).encode())
    for path in paths: digest.update(path.read_bytes())
    for name in ('evidence_pipeline.py', 'evidence_resolution.py', 'zoom_tools.py', 'vision_client.py', 'domain.py', 'imaging.py', 'inspect_images.py', 'execution.py', 'reference_images.py','provider_config.py'):
        digest.update(Path(__file__).with_name(name).read_bytes())
    native = Path(__file__).with_name('native_client.py')
    if native.exists(): digest.update(native.read_bytes())
    digest.update(PROMPT.read_bytes()); digest.update((PROMPT.parent / 'verification.txt').read_bytes()); key = digest.hexdigest()
    if dest.exists() and not force:
        try:
            cached = json.loads(dest.read_text(encoding='utf-8'))
            if cached.get('item_id') == item and cached.get('fingerprint') == key and cached.get('status') == 'completed' and cached.get('refinement', {}).get('status') in ('completed', 'not_needed'):
                validate_completed(cached, paths)
                return cached, True
        except (ValueError, KeyError, TypeError, OSError, IndexError): pass
    final = copy.deepcopy(baseline); stages = []; flow = {'version': 2, 'stage_limit': 3, 'ledger': [], 'resolutions': {}}
    response_metadata = None
    if baseline['status'] == 'completed':
        try:
            validate_completed(baseline, paths)
            plan = plan_zoom(paths, baseline)
            flow.update(triggers=plan['triggers'], requests=plan['requests'], skipped_requests=plan['skipped_requests'],
                        skipped_evidence_ids=[e['evidence_id'] for e in plan['evidence'] if e['zoom_status'] == 'skipped_budget'])
            before = Inspection.model_validate(baseline['result'])
            flow['ledger'] = register_result(before, baseline['input_images'], 'overview')
            decisions = {c: getattr(before, c) for c in CATEGORIES}
            if plan['triggers'] or review_all:
                task = {'phase': 'zoom_review', 'purpose': '核对所有类别与可见证据，候选不是标准答案；说明正常结构等替代解释',
                        'candidate_evidence': plan['evidence'], 'triggers': plan['triggers'],
                        'skipped_evidence_ids': flow['skipped_evidence_ids']}
                with execution_scope(execution, item):
                    review, meta = analyze(plan['entries'], model, base_url, timeout, thinking, task=task, schema=Verification)
                response_metadata = meta
                if execution: execution.check(item)
                stages.append({'review': review, 'metadata': meta, 'entries': plan['entries']})
                response_metadata = None
                flow['ledger'] += register_result(review.inspection, plan['entries'], 'zoom')
                conflicts = []
                for category in CATEGORIES:
                    same_region = covers_prior_positive(baseline, review, plan['entries'], category) if getattr(before, category) == 'present' else True
                    decision = resolve_pair(claim(before, flow['ledger'], category, 'overview'),
                                            claim(review.inspection, flow['ledger'], category, 'zoom'), same_region)
                    data = decision.to_dict(); data['same_region_coverage'] = same_region
                    flow['resolutions'][category] = data; decisions[category] = decision.state
                    if decision.needs_adjudication: conflicts.append(category)
                if conflicts:
                    task = {'phase': 'adjudication', 'purpose': '一次性核对冲突；证据不足或仍矛盾必须uncertain。resolved_evidence_ids须引用支持与反对证据ID，不能按票数决定',
                            'conflicts': {c: flow['resolutions'][c] for c in conflicts}, 'evidence_ledger': flow['ledger'],
                            'prior_assessments': {c: a.model_dump() for c, a in review.assessments.items()}}
                    with execution_scope(execution, item):
                        adjudicated, meta = analyze(plan['entries'], model, base_url, timeout, thinking, task=task, schema=Verification)
                    response_metadata = meta
                    if execution: execution.check(item)
                    stages.append({'review': adjudicated, 'metadata': meta, 'entries': plan['entries']}); response_metadata = None
                    flow['ledger'] += register_result(adjudicated.inspection, plan['entries'], 'adjudication')
                    for category in conflicts:
                        assessment = adjudicated.assessments[category]
                        required = set(flow['resolutions'][category]['evidence_ids'])
                        cited = set(assessment.resolved_evidence_ids)
                        # 明确结论必须处理冲突双方。格式合规也不意味着模型视觉判断已人工验证。
                        valid = required <= cited and cited <= {e['evidence_id'] for e in flow['ledger']}
                        state = getattr(adjudicated.inspection, category)
                        decisions[category] = state if valid else 'uncertain'
                        flow['resolutions'][category].update(adjudicated_state=state, final_state=decisions[category],
                                                             adjudication_evidence_complete=valid,
                                                             resolved_evidence_ids=assessment.resolved_evidence_ids)
                    # 裁决时若另一类别出现新分歧，也不能静默锁定旧结论；不再追加调用。
                    for category in set(CATEGORIES)-set(conflicts):
                        if decisions[category] == 'uncertain' or decisions[category] == getattr(adjudicated.inspection, category): continue
                        prior_evidence = {'evidence': [e for e in flow['ledger'] if e['stage'] == 'zoom' and e['kind'] == 'finding']}
                        covered = covers_prior_positive(prior_evidence, adjudicated, plan['entries'], category) if decisions[category] == 'present' else True
                        secondary = resolve_pair(claim(review.inspection, flow['ledger'], category, 'zoom'),
                                                 claim(adjudicated.inspection, flow['ledger'], category, 'adjudication'), covered)
                        decisions[category] = 'uncertain' if secondary.needs_adjudication else secondary.state
                        flow['resolutions'][category]['last_stage_change'] = {**secondary.to_dict(), 'final_state': decisions[category],
                                                                                 'additional_call_allowed': False}
                flow['semantic_stages'] = ['overview', 'zoom'] + (['adjudication'] if conflicts else [])
            else: flow['semantic_stages'] = ['overview']
            for category in CATEGORIES:
                skipped = [e for e in plan['evidence'] if e['category'] == category and e['zoom_status'] == 'skipped_budget']
                if skipped and decisions[category] == 'not_observed':
                    decisions[category] = 'uncertain'
                    flow.setdefault('limitations', []).append(category + ' 存在未核验候选，不能静默记为阴性')
                if category in flow['resolutions']: flow['resolutions'][category]['final_state'] = decisions[category]
            final = assemble(baseline, stages, decisions, flow)
            validate_completed(final, paths)
        except Exception as exc:
            # 已知响应和失败响应的用量不丢失；流程失败不能当成可评分的成功结论。
            if stages:
                states = {c: 'uncertain' for c in CATEGORIES}
                flow.setdefault('limitations', []).append('后续处理失败，保留已有候选并降为不确定。')
                final = assemble(baseline, stages, states, flow)
            else: final = copy.deepcopy(baseline)
            final['refinement'] = {**final.get('refinement', {}), 'status': 'failed', 'policy': 'bounded_evidence',
                                   'error': str(exc) if isinstance(exc, RuntimeError) else '证据处理失败：' + type(exc).__name__}
            known = getattr(exc, 'metadata', None) or response_metadata
            if known:
                final['refinement']['failed_stage_metadata'] = known
                meta = final.setdefault('metadata', {})
                meta['usage'] = {k: meta.get('usage', {}).get(k, 0) + known.get('usage', {}).get(k, 0) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
                meta['request_count'] = meta.get('request_count', 0) + known.get('request_count', 0)
                meta['requests_with_unknown_usage'] = meta.get('requests_with_unknown_usage', 0) + known.get('requests_with_unknown_usage', 0)
            final['evidence_flow'] = flow
    final.update(baseline_fingerprint=baseline.get('fingerprint'), fingerprint=key,
                 review_elapsed_seconds=round(time.monotonic() - started, 3))
    final['elapsed_seconds'] = round(baseline.get('elapsed_seconds', 0) + final['review_elapsed_seconds'], 3)
    atomic_json(dest, final)
    return final, False


def run_evidence_review(input_root, baseline_path, output, workers=3, model='deepseek-flash', base_url='https://api.deepseek.com', timeout=180, thinking='enabled', force=False, execution=None, review_all=False):
    baseline = json.loads(Path(baseline_path).read_text(encoding='utf-8')); groups = discover(input_root)
    records = baseline['records']; ids = [r['item_id'] for r in records]; expected = baseline.get('expected_items', ids)
    if not records or len(set(ids)) != len(ids) or len(set(expected)) != len(expected) or set(ids) != set(expected) or set(ids) - groups.keys():
        raise ValueError('初判批次物体集合不完整或重复')
    if not 1 <= workers <= 8: raise ValueError('workers必须为1至8')
    output.mkdir(parents=True, exist_ok=True); finished = {}; started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {pool.submit(process_one, r, groups[r['item_id']], output, model, base_url, timeout, thinking, force, execution, review_all): r['item_id'] for r in records}
        for job in as_completed(jobs):
            record, cached = job.result()
            if record['item_id'] != jobs[job]: raise ValueError('证据核验物体ID不一致')
            finished[record['item_id']] = record; ordered = [finished[i] for i in ids if i in finished]
            summary = {'schema_version': '2.0', 'pipeline': 'bounded_evidence', 'expected_items': ids,
                       'processed_groups': len(ordered), 'review_wall_seconds': round(time.monotonic() - started, 3),
                       'baseline_wall_seconds': baseline.get('wall_seconds'), 'records': ordered,
                       'configuration': {'model': model, 'review_thinking': thinking, 'workers': workers, 'review_all': review_all, 'max_zoom_crops': 6, 'max_semantic_stages': 3}}
            if execution: summary['execution'] = execution.snapshot()
            atomic_json(output / 'summary.json', summary); render_report(output, ordered)
            print(json.dumps({'item_id': record['item_id'], 'status': 'cached' if cached else record['status'],
                              'review_status': record.get('refinement', {}).get('status'), 'processed_groups': len(ordered), 'expected_groups': len(ids)}, ensure_ascii=False), flush=True)
    return int(any(r['status'] != 'completed' or r.get('refinement', {}).get('status') == 'failed' for r in finished.values()))
