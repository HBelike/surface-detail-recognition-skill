"""离线复算横评结果，核对账本与冻结文件，并生成可读完整报告。"""
import argparse
import hashlib
import html
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from domain import Inspection, Verification
from inspect_images import atomic_json
from compare_models import score_trials, ITEMS, ORDER
from assertions import grade_summary


def stage_metadata(trial):
    record = trial['record']; refinement = record.get('refinement', {})
    if 'baseline_metadata' not in refinement:
        stages = [('overview', record.get('metadata', {}))]
        if 'failed_stage_metadata' in refinement: stages.append(('failed', refinement['failed_stage_metadata']))
        return stages
    stages = [('overview', refinement['baseline_metadata'])]
    if 'review_metadata' in refinement: stages.append(('zoom', refinement['review_metadata']))
    if 'adjudication' in refinement: stages.append(('adjudication', refinement['adjudication']['metadata']))
    if 'failed_stage_metadata' in refinement: stages.append(('failed', refinement['failed_stage_metadata']))
    return stages


def details(trials):
    result = {'models': Counter(), 'stages': 0, 'stages_with_response': 0, 'first_response_contract_valid': 0,
              'stages_with_content_retry': 0, 'normalized_stages': 0, 'raw_responses': 0,
              'failed_tasks': [], 'stage_count': Counter(), 'skipped_candidates': 0, 'decided_to_uncertain': 0,
              'actual_request_ids': [], 'missing_usage_responses': 0}
    result['usage_detail_fields'] = {name: {'known_sum': 0, 'responses_with_field': 0}
                                     for name in ('image_tokens', 'reasoning_tokens', 'cached_tokens')}
    for trial in trials:
        record = trial['record']
        if record['status'] != 'completed' or record.get('refinement', {}).get('status') == 'failed':
            result['failed_tasks'].append({'item_id': trial['item_id'], 'round': trial['round'],
                                          'error': record.get('error') or record.get('refinement', {}).get('error')})
        result['skipped_candidates'] += len(record.get('evidence_flow', {}).get('skipped_evidence_ids', []))
        before = trial.get('baseline_result')
        if before and record.get('result'):
            result['decided_to_uncertain'] += sum(before[c] != 'uncertain' and record['result'][c] == 'uncertain' for c in ('linear_trace', 'material_loss'))
        for stage, metadata in stage_metadata(trial):
            result['stages'] += 1; result['stage_count'][stage] += 1
            responses = metadata.get('responses', [])
            result['raw_responses'] += len(responses)
            result['stages_with_content_retry'] += metadata.get('content_attempts', 0) > 1
            result['normalized_stages'] += bool(metadata.get('safe_normalizations'))
            result['actual_request_ids'].extend(metadata.get('request_ids', []))
            result['models'].update(metadata.get('response_models', []))
            if responses:
                result['stages_with_response'] += 1
                try:
                    schema = Inspection if stage == 'overview' else Verification
                    schema.model_validate_json(responses[0].get('final_content') or '')
                    result['first_response_contract_valid'] += responses[0].get('finish_reason') == 'stop'
                except ValueError: pass
            result['missing_usage_responses'] += sum(r.get('usage') is None for r in responses)
            for response in responses:
                usage = response.get('usage') or {}
                prompt = usage.get('prompt_tokens_details') or {}
                completion = usage.get('completion_tokens_details') or {}
                cached = prompt.get('cached_tokens')
                if cached is None: cached = usage.get('cached_tokens')
                if cached is None: cached = usage.get('prompt_cache_hit_tokens')
                fields = {'image_tokens': prompt.get('image_tokens'), 'reasoning_tokens': completion.get('reasoning_tokens'), 'cached_tokens': cached}
                for name, value in fields.items():
                    if isinstance(value, int):
                        result['usage_detail_fields'][name]['known_sum'] += value
                        result['usage_detail_fields'][name]['responses_with_field'] += 1
    durations = sorted(t['wall_seconds'] for t in trials)
    result['median_case_seconds'] = round(statistics.median(durations), 3) if durations else None
    result['p95_case_seconds'] = durations[math.ceil(len(durations) * .95) - 1] if durations else None
    result['response_ids_unique'] = len(result['actual_request_ids']) == len(set(result['actual_request_ids']))
    result['first_response_contract_rate'] = result['first_response_contract_valid'] / result['stages_with_response'] if result['stages_with_response'] else None
    return result


def audit(matrix):
    raw = json.loads((matrix / 'results.json').read_text(encoding='utf-8'))
    protocol = json.loads((matrix / 'protocol.json').read_text(encoding='utf-8'))
    recorded_runtime = Path(protocol.get('runtime_source_root', matrix.parent / 'runtime/surface-defect-inspection'))
    portable_runtime = matrix.parent / recorded_runtime.parent.name / recorded_runtime.name
    runtime = portable_runtime if portable_runtime.exists() else recorded_runtime
    changed = [name for name, sha in protocol['sources'].items() if hashlib.sha256((runtime / name).read_bytes()).hexdigest() != sha]
    if changed: raise ValueError('运行副本在实验后发生变化')
    if hashlib.sha256((matrix / 'human-labels-scoring-only.json').read_bytes()).hexdigest() != protocol['labels_sha256']:
        # 序列化空白可能不同；必须再核对原始标签及内容。
        label_original = matrix.parents[2] / 'review/labels-development-v2.json'
        if not label_original.exists() or hashlib.sha256(label_original.read_bytes()).hexdigest() != protocol['labels_sha256']:
            raise ValueError('原始人工标签哈希变化')
        if json.loads(label_original.read_text(encoding='utf-8')) != raw['labels']: raise ValueError('评分标签内容发生变化')
    recomputed = {}; richer = {}
    seen = set()
    for t in raw['trials']:
        key = (t['phase'], t['provider'], t['round'], t['item_id'])
        if key in seen: raise ValueError('横评任务重复')
        seen.add(key)
    expected_tasks = 90 if protocol['requested_phase'] == 'all' else 54
    if raw['status'] == 'completed' and len(seen) != expected_tasks:
        raise ValueError('声称完成但任务数量不完整')
    for phase, values in raw['scores'].items():
        trials = [t for t in raw['trials'] if t['phase'] == phase]
        recomputed[phase] = score_trials(trials, raw['labels'], tuple(values))
        if recomputed[phase] != values: raise ValueError('独立复算与运行评分不一致')
        richer[phase] = {n: details([t for t in trials if t['provider'] == n]) for n in values}
    known = {k: sum(t['execution']['known_usage'][k] for t in raw['trials']) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    requests = sum(t['execution']['api_calls'] for t in raw['trials'])
    if known != raw['execution']['known_usage'] or requests != raw['execution']['api_calls']:
        raise ValueError('物体账本与全局账本不一致，禁止报告完整用量')
    paired_baselines = {}
    for name in raw['scores'].get('B', {}):
        rounds = []
        for number in range(1, 4):
            trials = [t for t in raw['trials'] if t['phase'] == 'B' and t['provider'] == name and t['round'] == number]
            records = [{'item_id': t['item_id'], 'status': 'completed' if t.get('baseline_result') else 'failed', 'result': t.get('baseline_result')} for t in trials]
            rounds.append(grade_summary({'records': records}, raw['labels']))
        paired_baselines[name] = {'rounds': rounds, 'item_correct': sum(r['item_correct'] for r in rounds),
                                  'category_correct': sum(r['category_correct'] for r in rounds),
                                  'paired_item_delta': raw['scores']['B'][name]['item_correct'] - sum(r['item_correct'] for r in rounds),
                                  'paired_category_delta': raw['scores']['B'][name]['category_correct'] - sum(r['category_correct'] for r in rounds)}
    audit_data = {'status': raw['status'], 'task_count': len(seen), 'planned_tasks': 90 if protocol['requested_phase'] == 'all' else 54,
                  'frozen_runtime_unchanged': True, 'label_hash_verified': True, 'scores_recomputed_identically': True,
                  'known_usage': known, 'http_requests': requests, 'global_execution': raw['execution'], 'details': richer,
                  'B_paired_baseline_scores': paired_baselines,
                  'confusion_counting': 'FN包含阳性样本的拒判和失败；uncertain/failed是交叉统计，不能与TP/FP/TN/FN简单相加'}
    atomic_json(matrix / 'audit.json', audit_data)
    return raw, audit_data


def render(matrix, raw, audit_data):
    escape = html.escape
    sections = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>三模型横评实验</title><style>body{font:15px/1.7 system-ui;margin:0;background:#f3f6fa;color:#16263b}main{max-width:1280px;margin:auto;padding:32px}section{background:white;border:1px solid #dce4ee;border-radius:12px;padding:24px;margin:22px 0}h1{font-size:30px}h2{font-size:22px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:11px;border-bottom:1px solid #e1e8f0;text-align:left}th{background:#edf3fa}code{color:#215a92}small,.note{color:#53657a}.wrap{overflow:auto}.bad{color:#b02b40}.good{color:#137459}details{margin:12px 0}pre{white-space:pre-wrap;word-break:break-word}a{color:#215a92}</style><main><h1>三模型真实横评实验</h1>',
                '<p class="note">Qwen3.8-Max-0902 · DeepSeek Flash · Kimi K2.7 Code｜六组人工开发标签 × 三轮。Kimi K3.1 Pro在当前Key下返回404，因此明确使用已通过图像冒烟的K2.7 Code。</p>',
                f'<section><b>执行状态：{escape(raw["status"])}</b><p>{len(raw["trials"])} 个任务，{audit_data["http_requests"]} 次HTTP请求，已知 {audit_data["known_usage"]["total_tokens"]:,} tokens。用量、冻结源码和评分均经离线复核。</p><p class="note">失败、缺失和拒判保留全分母；无人工位置框，不报告定位准确率。六组均为开发数据，结果不代表泛化能力。实际账单未知，不将tokens直接称为已扣费用。</p></section>']
    truth = {r['item_id']: r for r in raw['labels']['labels']}; state = {'present': '有', 'not_observed': '未见', 'uncertain': '不确定'}
    for phase, scores in raw['scores'].items():
        sections += [f'<section><h2>{phase} · ' + ('同图单次识别' if phase == 'A' else '前两名独立完整工程流程') + '</h2><div class="wrap"><table><tr><th>模型</th><th>整组一致</th><th>类别一致</th><th>三轮均正确</th><th>逐轮整组</th><th>完成</th><th>请求</th><th>已知tokens</th><th>开发门槛</th></tr>']
        for n, s in scores.items():
            sections.append(f'<tr><td>{escape(n)}</td><td>{s["item_correct"]}/18 ({s["item_correct"]/18:.1%})</td><td>{s["category_correct"]}/36 ({s["category_correct"]/36:.1%})</td><td>{s["three_round_correct_items"]}/6</td><td>{" · ".join(str(r["item_correct"])+"/6" for r in s["rounds"])}</td><td>{s["completed_tasks"]}/18</td><td>{s["http_requests"]}</td><td>{s["known_usage"]["total_tokens"]:,}</td><td class="'+('good' if s['gate_pass'] else 'bad')+'">'+('通过' if s['gate_pass'] else '未通过')+'</td></tr>')
        sections += ['</table></div><p class="note">开发门槛：每轮至少5/6整组正确、合计至少15/18、三轮均正确至少4/6；这是工程预设门槛，并非考官官方评分。</p><div class="wrap"><table><tr><th>物体</th><th>人工（线状 / 缺损）</th>'+''.join('<th>'+escape(n)+'（轮1、2、3）</th>' for n in scores)+'</tr>']
        for item in ITEMS:
            sections.append('<tr><td>'+item+'</td><td>'+' / '.join(state[truth[item][c]] for c in ('linear_trace','material_loss'))+'</td>')
            for n in scores:
                cases = sorted([t for t in raw['trials'] if t['phase'] == phase and t['provider'] == n and t['item_id'] == item], key=lambda t:t['round'])
                values = []
                for t in cases:
                    r = t['record']; failed = r['status'] != 'completed' or r.get('refinement',{}).get('status') == 'failed'
                    path = f'{phase}/{n}/round-{t["round"]}/{item}.json'
                    value = '失败' if failed else ' / '.join(state[r['result'][c]] for c in ('linear_trace','material_loss'))
                    values.append('<a href="'+path+'">'+value+'</a>')
                sections.append('<td>'+'；'.join(values)+'</td>')
            sections.append('</tr>')
        sections += ['</table></div>']
        for n, s in scores.items():
            d = audit_data['details'][phase][n]
            if phase == 'B':
                initial = audit_data['B_paired_baseline_scores'][n]
                sections.append('<p><b>'+escape(n)+' · 同一B任务的初判→最终：</b>整组 '+str(initial['item_correct'])+'/18 → '+str(s['item_correct'])+'/18；类别 '+str(initial['category_correct'])+'/36 → '+str(s['category_correct'])+'/36。此配对增益来自B自己的新初判，与A没有混用。</p>')
            sections += ['<details><summary>'+escape(n)+' · 逐类指标、合同与模型版本</summary><pre>'+escape(json.dumps({'confusion':s['confusion'],'stage_changes':s['stage_changes'], **d},ensure_ascii=False,indent=2))+'</pre></details>']
        sections.append('</section>')
    sections += ['<section><h2>复现与边界</h2><p>同一EXIF校正、RGB JPEG95/subsampling0完整图；A不裁剪、不复核，B重新调用初判后使用统一的最多6裁剪和一次裁决。人工标签只供离线评分，模型输入不含标签、历史答案或正常参照。供应商内部视觉预算、Thinking强度和缓存不同，不宣称等算力。</p><p>冻结协议、原始最终回答、用量、失败与独立审计分别见 <a href="protocol.json">protocol.json</a>、<a href="results.json">results.json</a>、<a href="audit.json">audit.json</a>。不保存内部推理、密钥或Base64请求体。完全同分的模型按预先冻结顺序入围，不能据此宣布通用能力高低。</p></section></main></html>']
    (matrix / 'comparison.html').write_text(''.join(sections),encoding='utf-8')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--matrix',type=Path,required=True)
    args=parser.parse_args();raw,audit_data=audit(args.matrix);render(args.matrix,raw,audit_data)
    print(json.dumps({k:v for k,v in audit_data.items() if k!='details'},ensure_ascii=False,indent=2))
