"""冻结三供应商协议，执行同图三轮横评及前两名独立工程流程实验。"""
import argparse
import hashlib
import html
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from domain import Inspection
from execution import ExecutionBudget
from imaging import discover, prepare_images
from inspect_images import atomic_json, run_item
from evidence_pipeline import process_one
from provider_config import PROVIDERS
from vision_client import PROMPT
from assertions import grade_summary, CATEGORIES

ORDER = ('qwen', 'deepseek', 'kimi')
ITEMS = ('item-p01', 'item-p08', 'item-p10', 'item-p14', 'item-p17', 'item-p25')
LABEL_SHA256 = '69dbb2dafa5e2aacc4874ce33e10a13a6f850332306342eed25a235f7f51c02d'


def sha(data): return hashlib.sha256(data).hexdigest()


def schedule(providers):
    """每物体一个块；轮换提交顺序，每家在块内并发恒为1。"""
    return [{'round': r + 1, 'item_id': item,
             'providers': list(providers[(r + i) % len(providers):] + providers[:(r + i) % len(providers)])}
            for r in range(3) for i, item in enumerate(ITEMS)]


def protocol(input_root, labels_path):
    groups = discover(input_root)
    labels = json.loads(labels_path.read_text(encoding='utf-8'))
    if sha(labels_path.read_bytes()) != LABEL_SHA256:
        raise ValueError('本协议固定已确认的development-v2标签；更换标签需建立新协议版本')
    if labels.get('source') != 'human' or {r['item_id'] for r in labels['labels'] if r.get('reviewed')} != set(ITEMS):
        raise ValueError('本实验固定六组人工v2标签，不允许静默改变样本')
    images = {}
    for item in ITEMS:
        entries = prepare_images(groups[item], 'baseline')
        images[item] = [{'source': p.name, 'original_sha256': sha(p.read_bytes()),
                         'encoded_sha256': sha(e['data_url'].encode()),
                         **{k: v for k, v in e.items() if k != 'data_url'}} for p, e in zip(groups[item], entries)]
    sources = {str(p.relative_to(ROOT)).replace('\\', '/'): sha(p.read_bytes())
               for folder in ('scripts', 'references', 'evals') for p in sorted((ROOT / folder).rglob('*'))
               if p.is_file() and '__pycache__' not in p.parts and p.suffix in ('.py', '.txt', '.md', '.json')}
    return {'version': 'three-model-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
            'runtime_source_root': str(ROOT),
            'input_root': str(input_root), 'items': list(ITEMS), 'rounds': 3,
            'labels_sha256': sha(labels_path.read_bytes()), 'labels_role': 'development-v2',
            'prompt_sha256': sha(PROMPT.read_bytes()), 'schema_sha256': sha(json.dumps(Inspection.model_json_schema(), sort_keys=True).encode()),
            'sources': sources, 'images': images,
            'providers': {n: {**PROVIDERS[n].snapshot(), 'parameters': PROVIDERS[n].parameters()} for n in ORDER},
            'A_schedule': schedule(ORDER), 'A_tasks': 54, 'B_tasks': 36,
            'B_selection': '依次整组正确数、类别正确数、三轮全正确物体数；完全同分时按预先冻结qwen/deepseek/kimi顺序选两个，声明并列',
            'limits': {'global_http_requests': 300, 'global_seconds': 14400, 'A_case_http_requests': 6, 'B_case_http_requests': 18, 'case_seconds': 900, 'timeout_seconds': 180},
            'cache': '禁止本地结果复用；供应商缓存不可控',
            'network_route': 'direct_NO_PROXY_star' if os.environ.get('NO_PROXY') == '*' else 'environment_or_system_proxy',
            'inputs': '相同原图EXIF校正RGB JPEG95/subsampling0，不裁剪不缩放；无人工标签、历史答案、正常参照、工具调用',
            'retry': '每语义阶段最多2次内容尝试，每次最多3次HTTP尝试；失败保留全分母',
            'thinking': '供应商支持的显式质量参数，不宣称等内部视觉预算或等推理算力',
            'localization': '没有人工框，不能报告定位准确率'}


class CaseBudget(ExecutionBudget):
    def __init__(self, global_budget, provider, phase):
        super().__init__(6 if phase == 'A' else 18, 900, 900)
        self.parent = global_budget
        self.provider_config = PROVIDERS[provider]
        self.reasoning_effort = 'high'
        self.capture_responses = True

    def reserve_request(self, item, timeout):
        self.check(item)
        if self.calls >= self.max_api_calls: raise RuntimeError('物体请求预算已耗尽')
        parent_timeout = self.parent.reserve_request('experiment', timeout)
        return super().reserve_request(item, parent_timeout)

    def record_response(self, usage):
        super().record_response(usage)
        self.parent.record_response(usage)


def execute_case(block, name, phase, groups, output, budget, frozen):
    item = block['item_id']; provider = PROVIDERS[name]
    dest = output / phase / name / f'round-{block["round"]}'
    execution = CaseBudget(budget, name, phase)
    for path, expected in zip(groups[item], frozen['images'][item]):
        if sha(path.read_bytes()) != expected['original_sha256']: raise ValueError('原图在实验中发生变化')
    args = SimpleNamespace(mode='baseline', model=provider.model, base_url=provider.base_url,
                           thinking='enabled', reasoning_effort='high', backend='deepseek',
                           execution=execution, force=True, timeout=180)
    started = time.monotonic()
    baseline, cached = run_item(item, groups[item], args, dest / 'baseline' if phase == 'B' else dest)
    if cached: raise RuntimeError('横评禁止缓存')
    final = baseline
    if phase == 'B':
        final, cached = process_one(baseline, groups[item], dest, provider.model, provider.base_url,
                                    180, 'enabled', True, execution)
        if cached: raise RuntimeError('工程复查禁止缓存')
    return {'phase': phase, 'provider': name, 'round': block['round'], 'item_id': item,
            'record': final, 'baseline_result': baseline.get('result') if phase == 'B' else None,
            'execution': execution.snapshot(), 'wall_seconds': round(time.monotonic() - started, 3)}


def score_trials(trials, labels, providers):
    report = {}
    for name in providers:
        selected = [t for t in trials if t['provider'] == name]
        rounds = []
        for r in range(1, 4):
            records = [t['record'] for t in selected if t['round'] == r]
            rounds.append(grade_summary({'records': records}, labels))
        cases = [c for r in rounds for c in r['cases']]
        confusion = {}
        for category in CATEGORIES:
            values = [c['categories'][category] for c in cases]
            counts = {'TP': 0, 'FP': 0, 'TN': 0, 'FN': 0, 'uncertain': 0, 'failed_or_missing': 0}
            for value in values:
                actual, predicted = value['truth'], value['predicted']
                counts['uncertain'] += predicted == 'uncertain'
                counts['failed_or_missing'] += predicted is None
                if actual == 'present': counts['TP' if predicted == 'present' else 'FN'] += 1
                elif predicted == 'present': counts['FP'] += 1
                elif predicted == 'not_observed': counts['TN'] += 1
            counts['precision'] = counts['TP'] / (counts['TP'] + counts['FP']) if counts['TP'] + counts['FP'] else None
            counts['recall'] = counts['TP'] / (counts['TP'] + counts['FN']) if counts['TP'] + counts['FN'] else None
            confusion[category] = counts
        stable_correct = sum(all(next(c for c in r['cases'] if c['item_id'] == item)['item_exact'] for r in rounds) for item in ITEMS)
        stable_outputs = 0
        for item in ITEMS:
            rows = [t['record'] for t in selected if t['item_id'] == item]
            if len(rows) == 3 and all(x['status'] == 'completed' and x.get('refinement', {}).get('status') != 'failed' for x in rows):
                stable_outputs += len({tuple(x['result'][c] for c in CATEGORIES) for x in rows}) == 1
        usage = {k: sum(t['execution']['known_usage'][k] for t in selected) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
        changed = {'correct_to_wrong': 0, 'wrong_to_correct': 0, 'unchanged_correct': 0, 'unchanged_wrong': 0}
        truth = {r['item_id']: r for r in labels['labels']}
        for t in selected:
            if not t.get('baseline_result'): continue
            record = t['record']; completed = record['status'] == 'completed' and record.get('refinement', {}).get('status') != 'failed'
            for c in CATEGORIES:
                before = t['baseline_result'][c] == truth[t['item_id']][c]
                after = completed and record.get('result', {}).get(c) == truth[t['item_id']][c]
                changed['unchanged_correct' if before and after else 'correct_to_wrong' if before else 'wrong_to_correct' if after else 'unchanged_wrong'] += 1
        report[name] = {'rounds': rounds, 'category_correct': sum(r['category_correct'] for r in rounds), 'category_total': 36,
                        'item_correct': sum(r['item_correct'] for r in rounds), 'item_total': 18,
                        'three_round_correct_items': stable_correct, 'stable_output_items': stable_outputs,
                        'completed_tasks': sum(t['record']['status'] == 'completed' and t['record'].get('refinement', {}).get('status') != 'failed' for t in selected),
                        'expected_tasks': 18, 'confusion': confusion, 'known_usage': usage,
                        'http_requests': sum(t['execution']['api_calls'] for t in selected),
                        'unknown_usage_requests': sum(t['execution']['requests_with_unknown_usage'] for t in selected),
                        'sum_case_seconds': round(sum(t['wall_seconds'] for t in selected), 3), 'stage_changes': changed,
                        'gate_pass': all(r['item_correct'] >= 5 for r in rounds) and sum(r['item_correct'] for r in rounds) >= 15 and stable_correct >= 4}
    return report


def select_finalists(scores):
    return sorted(scores, key=lambda n: (-scores[n]['item_correct'], -scores[n]['category_correct'],
                                         -scores[n]['three_round_correct_items'], ORDER.index(n)))[:2]


def write_report(output, report):
    lines = ['# 三模型真实横评', '', '六组人工v2开发标签；所有失败、缺失和拒判保留分母。不代表泛化或定位精度。', '',
             'Kimi K3.1 Pro在当前Key下404，本轮明确使用已验证图像能力的Kimi K2.7 Code。', '']
    for phase, values in report['scores'].items():
        lines += [f'## {phase}：' + ('单次识别' if phase == 'A' else '独立重新初判的工程流程'), '',
                  '|供应商|整组正确|类别正确|三轮全正确物体|完成任务|请求|已知tokens|门槛|', '|---|---:|---:|---:|---:|---:|---:|---|']
        for name, s in values.items():
            lines.append(f'|{name}|{s["item_correct"]}/18|{s["category_correct"]}/36|{s["three_round_correct_items"]}/6|{s["completed_tasks"]}/18|{s["http_requests"]}|{s["known_usage"]["total_tokens"]}|{"通过" if s["gate_pass"] else "未通过"}|')
        lines += ['', '各轮整组正确：' + '；'.join(n + ' ' + '/'.join(str(r['item_correct']) for r in s['rounds']) for n, s in values.items()), '',
                  '逐物体结果（P=存在，N=未发现，U=不确定，F=流程失败，顺序：线状/缺损）：', '', '|物体|人工|' + '|'.join(values) + '|', '|---|---|' + '|'.join('---' for _ in values) + '|']
        truth = {r['item_id']: r for r in report['labels']['labels']}
        state = {'present': 'P', 'not_observed': 'N', 'uncertain': 'U'}
        for item in ITEMS:
            row = [item, '/'.join(state[truth[item][c]] for c in CATEGORIES)]
            for name in values:
                ts = sorted([t for t in report['trials'] if t['phase'] == phase and t['provider'] == name and t['item_id'] == item], key=lambda t: t['round'])
                row.append('，'.join('/'.join(state[t['record']['result'][c]] for c in CATEGORIES) if t['record']['status'] == 'completed' and t['record'].get('refinement', {}).get('status') != 'failed' else 'F' for t in ts) or '待执行')
            lines.append('|' + '|'.join(row) + '|')
        if phase == 'B':
            lines += ['', '后续阶段对初判类别的影响：', '', '```json', json.dumps({n: s['stage_changes'] for n, s in values.items()}, ensure_ascii=False, indent=2), '```']
    lines += ['', '实际模型版本、逐类混淆统计、原始最终回答、每阶段用量见results.json和各物体JSON。未取得实际账单，不把tokens换算为已扣费用。',
              '供应商内部缩放、Thinking预算和缓存不同；外部输入一致不意味着内部算力一致。无人工框，不评价框正确率。',
              '并列按冻结顺序选两个进行工程比较，不能解释为通用能力排名。']
    text = '\n'.join(lines)
    (output / 'report.md').write_text(text, encoding='utf-8')
    # 保留完整数据，页面使用转义后的纯文本，不执行模型输出。
    (output / 'report.html').write_text('<!doctype html><meta charset="utf-8"><title>三模型横评</title><style>body{max-width:1200px;margin:32px auto;padding:20px;font:16px/1.7 system-ui;background:#f5f7fa;color:#182638}pre{white-space:pre-wrap;background:white;padding:24px;border-radius:12px}</style><h1>三模型真实横评</h1><pre>' + html.escape(text) + '</pre>', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--labels', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--phase', choices=['A', 'all'], default='all')
    args = parser.parse_args(argv)
    if args.execute:
        missing = [PROVIDERS[name].credential_env for name in ORDER if not os.environ.get(PROVIDERS[name].credential_env)]
        if missing: raise ValueError('缺少环境变量：' + ', '.join(missing))
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    if (output / 'protocol.json').exists(): raise ValueError('横评使用全新目录，避免混入旧结果')
    frozen = protocol(args.input.resolve(), args.labels)
    frozen['requested_phase'] = args.phase
    atomic_json(output / 'protocol.json', frozen)
    if not args.execute:
        print(json.dumps({'status': 'prepared_no_api_calls', 'A_tasks': 54, 'B_tasks': 36})); return 0
    labels = json.loads(args.labels.read_text(encoding='utf-8'))
    atomic_json(output / 'human-labels-scoring-only.json', labels)
    groups = discover(args.input.resolve()); global_budget = ExecutionBudget(300, 14400, 14400)
    report = {'status': 'running', 'labels': labels, 'scores': {}, 'trials': [], 'limits': frozen['limits']}
    for phase in ('A', 'B') if args.phase == 'all' else ('A',):
        names = ORDER if phase == 'A' else tuple(select_finalists(report['scores']['A']))
        blocks = schedule(names)
        if phase == 'B': atomic_json(output / 'B-selection-and-schedule.json', {'selected': names, 'schedule': blocks, 'A_scores': report['scores']['A']})
        for block in blocks:
            with ThreadPoolExecutor(max_workers=len(names)) as pool:
                jobs = [pool.submit(execute_case, block, n, phase, groups, output, global_budget, frozen) for n in block['providers']]
                for n, job in zip(block['providers'], jobs):
                    try: trial = job.result()
                    except Exception as exc:
                        # 未知本地异常不输出堆栈或请求体，保留预期任务为失败。
                        trial = {'phase': phase, 'provider': n, 'round': block['round'], 'item_id': block['item_id'],
                                 'record': {'item_id': block['item_id'], 'status': 'failed', 'error': '本地任务失败：' + type(exc).__name__},
                                 'execution': {'api_calls': 0, 'known_usage': {k: 0 for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}, 'requests_with_unknown_usage': 0}, 'wall_seconds': 0}
                    report['trials'].append(trial)
                    print(json.dumps({'phase': phase, 'provider': n, 'round': block['round'], 'item_id': block['item_id'],
                                      'status': trial['record']['status'], 'completed': len(report['trials']), 'http_requests': global_budget.calls}, ensure_ascii=False), flush=True)
            report['scores'][phase] = score_trials([t for t in report['trials'] if t['phase'] == phase], labels, names)
            report['execution'] = global_budget.snapshot()
            atomic_json(output / 'results.json', report); write_report(output, report)
        for n in names:
            for r in range(1, 4):
                dest = output / phase / n / f'round-{r}'
                rows = [t['record'] for t in report['trials'] if t['phase'] == phase and t['provider'] == n and t['round'] == r]
                atomic_json(dest / 'summary.json', {'expected_items': list(ITEMS), 'records': rows})
    report['status'] = 'completed'; report['execution'] = global_budget.snapshot()
    atomic_json(output / 'results.json', report); write_report(output, report)
    return 0


if __name__ == '__main__': raise SystemExit(main())
