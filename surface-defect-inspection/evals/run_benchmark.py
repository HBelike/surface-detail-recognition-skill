"""冻结旧新版本并准备交错三轮A/B；只有显式--execute才调用模型。"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from adapt_runs import adapt_run

EXPECTED_OLD_SHA = '3e0c755cfffb3f0c5d9a70e0b5c4014c8ad36b40ce308bd79e4e149a4f1ed445'


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def source_manifest(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and not any(part.startswith('.') or part == '__pycache__' for part in p.relative_to(root).parts)}


def prepare(old_zip, new_skill, input_root, labels_path, output):
    if output.exists(): raise ValueError('A/B输出目录必须全新，禁止复用旧缓存或覆盖旧评测')
    if output.resolve().is_relative_to(new_skill.resolve()) or output.resolve().is_relative_to(input_root.resolve()):
        raise ValueError('A/B输出须位于Skill和输入图片目录之外，避免递归复制或输入污染')
    archive_sha = hashlib.sha256(old_zip.read_bytes()).hexdigest()
    if archive_sha != EXPECTED_OLD_SHA: raise ValueError('旧正式包哈希与已冻结版本不一致')
    labels = json.loads(labels_path.read_text(encoding='utf-8-sig'))
    from assertions import grade_summary
    grade_summary({'records': []}, labels)
    items = [r['item_id'] for r in labels['labels'] if r.get('reviewed') is True and all(r.get(c) in ('present', 'not_observed') for c in ('linear_trace', 'material_loss'))]
    if not items: raise ValueError('没有两类均明确的人工标签')
    output.mkdir(parents=True)
    old_root = output / 'frozen-old'; new_root = output / 'frozen-new'
    with zipfile.ZipFile(old_zip) as archive:
        for info in archive.infolist():
            target = (old_root / info.filename).resolve()
            if not target.is_relative_to(old_root.resolve()): raise ValueError('旧包包含越界路径')
        archive.extractall(old_root)
    old_entry = list(old_root.rglob('scripts/run_skill.py'))
    if len(old_entry) != 1: raise ValueError('旧包主入口不唯一')
    shutil.copytree(new_skill, new_root, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.*'))
    sys.path.insert(0, str(new_skill / 'scripts'))
    from imaging import discover
    groups = discover(input_root)
    if set(items) - groups.keys(): raise ValueError('人工标签物体不在输入目录')
    inputs = {item: {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in groups[item]} for item in items}
    schedule = []
    for round_number in range(1, 4):
        for config in (('old_skill', 'new_skill') if round_number % 2 else ('new_skill', 'old_skill')):
            schedule.append({'round': round_number, 'config': config, 'output': f'eval-0/{config}/run-{round_number}'})
    protocol = {'status': 'prepared_not_executed', 'created_at': datetime.now(timezone.utc).isoformat(),
                'old_archive_sha256': archive_sha, 'old_entry': old_entry[0].relative_to(output).as_posix(),
                'new_entry': 'frozen-new/scripts/run_skill.py', 'items': items, 'input_root': str(input_root),
                'inputs': inputs, 'old_sources': source_manifest(old_entry[0].parents[1]), 'new_sources': source_manifest(new_root),
                'schedule': schedule, 'model': 'deepseek-flash', 'base_url': 'https://api.deepseek.com',
                'cache_policy': 'fresh_output_and_force', 'supplier_cache_controlled': False,
                'gate': {'item_min_per_round': 5, 'item_min_total': 15, 'stable_items_min': 4, 'no_new_positive_misses': True},
                'note': '当前固定为同一DeepSeek服务的工程A/B，跨供应商模型对照另设协议；已用开发集不作为泛化证明。'}
    dump(output / 'protocol.json', protocol); dump(output / 'human-labels.json', labels)
    return protocol


def release_gate(old_scores, new_scores):
    if len(old_scores) != 3 or len(new_scores) != 3: return {'passed': False, 'reason': '必须完整提供旧新各三轮'}
    old_items = [{c['item_id'] for c in s['cases'] if c['item_eligible']} for s in old_scores]
    new_items = [{c['item_id'] for c in s['cases'] if c['item_eligible']} for s in new_scores]
    if any(items != old_items[0] for items in old_items + new_items) or len(old_items[0]) != 6:
        return {'passed': False, 'reason': '固定六组开发集覆盖不一致，不能套用当前门槛'}
    if any(v['failed_or_missing'] for s in old_scores+new_scores for c in s['cases'] for v in c['categories'].values()):
        return {'passed': False, 'reason': '存在失败或缺失，工程A/B未完整完成，不替换正式包'}
    stable = set.intersection(*[{c['item_id'] for c in s['cases'] if c['item_exact']} for s in new_scores])
    new_misses = []
    for index, (old, new) in enumerate(zip(old_scores, new_scores), 1):
        previous = {c['item_id']: c for c in old['cases']}
        for case in new['cases']:
            for category, outcome in case['categories'].items():
                if outcome['truth'] == 'present' and previous[case['item_id']]['categories'][category]['correct'] and not outcome['correct']:
                    new_misses.append({'round': index, 'item_id': case['item_id'], 'category': category})
    passed = all(s['item_correct'] >= 5 for s in new_scores) and sum(s['item_correct'] for s in new_scores) >= 15 and len(stable) >= 4 and not new_misses
    return {'passed': passed, 'item_correct_per_round': [s['item_correct'] for s in new_scores],
            'item_correct_total': sum(s['item_correct'] for s in new_scores), 'stable_items': sorted(stable), 'new_positive_misses': new_misses,
            'note': '仅为六组已调参开发集的替换门槛，不代表未知样本准确率。未通过不自动重新采样。'}


def execute(output):
    protocol = json.loads((output / 'protocol.json').read_text(encoding='utf-8'))
    if protocol['status'] != 'prepared_not_executed': raise ValueError('协议已执行或执行中；禁止在同一目录再次采样')
    if not os.getenv('DEEPSEEK_API_KEY'): raise ValueError('请先在当前进程配置DEEPSEEK_API_KEY；本次未执行')
    if source_manifest(Path(__file__).resolve().parents[1]) != protocol['new_sources']:
        raise ValueError('当前工程与冻结新版不同，请从冻结新版的evals/run_benchmark.py执行')
    # 执行前再次验证两份冻结代码和全部评分图，避免计划准备后发生漂移。
    old_root = (output / protocol['old_entry']).parents[1]
    for root, name in ((old_root, 'old_sources'), (output / 'frozen-new', 'new_sources')):
        if source_manifest(root) != protocol[name]: raise ValueError('冻结代码发生变化')
    root = Path(protocol['input_root'])
    for item, files in protocol['inputs'].items():
        actual={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/item).iterdir()
                if p.is_file() and p.suffix.lower() in ('.png','.jpg','.jpeg','.webp') and not p.name.startswith('.')}
        if actual!=files:raise ValueError('评分图像发生变化或视角集合改变')
    protocol['status'] = 'running'; dump(output / 'protocol.json', protocol)
    scores = {'old_skill': [], 'new_skill': []}
    for entry in protocol['schedule']:
        destination = output / entry['output']
        if not destination.resolve().is_relative_to(output.resolve()):raise ValueError('评测输出路径越界')
        destination.mkdir(parents=True, exist_ok=False)
        skill_entry = protocol['old_entry'] if entry['config'] == 'old_skill' else protocol['new_entry']
        command = [sys.executable, '-X', 'utf8', str((output / skill_entry).resolve()), '--input', str(root), '--output', str((destination / 'results').resolve()),
                   '--items', *protocol['items'], '--workers', '3', '--timeout', '180', '--model', protocol['model'], '--base-url', protocol['base_url'], '--force']
        started = time.monotonic()
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',
                                 **({'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}))
        try:
            stdout,stderr=process.communicate(timeout=2400)
            (destination / 'stdout.log').write_text(stdout, encoding='utf-8')
            (destination / 'stderr.log').write_text(stderr, encoding='utf-8')
            exit_code = process.returncode
        except subprocess.TimeoutExpired:
            if os.name=='nt':
                subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,check=False)
            else:
                import signal
                os.killpg(process.pid,signal.SIGKILL)
            process.communicate()
            exit_code = 124
            (destination / 'stderr.log').write_text('A/B单轮总时间上限已到。', encoding='utf-8')
        elapsed = round(time.monotonic() - started, 3)
        summary_path = destination / 'results/summary.json'
        summary = json.loads(summary_path.read_text(encoding='utf-8')) if summary_path.exists() else {'records': []}
        summary.update(benchmark_wall_seconds=elapsed, benchmark_exit_code=exit_code)
        # 不完整批次仍按全部人工标签计分，不让缺失物体消失。
        dump(destination / 'scoring-summary.json', summary)
        score = adapt_run(destination / 'scoring-summary.json', output / 'human-labels.json', destination)
        scores[entry['config']].append((entry['round'], score))
        print(json.dumps({'config': entry['config'], 'round': entry['round'], 'exit_code': exit_code, 'item_correct': score['item_correct']}, ensure_ascii=False), flush=True)
    old = [s for _, s in sorted(scores['old_skill'])]; new = [s for _, s in sorted(scores['new_skill'])]
    gate = release_gate(old, new); dump(output / 'release-gate.json', gate)
    from aggregate_results import aggregate
    aggregate(output)
    protocol['status'] = 'executed_passed' if gate['passed'] else 'executed_not_passed'
    dump(output / 'protocol.json', protocol)
    return gate


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-zip', type=Path); parser.add_argument('--new-skill', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--input', type=Path); parser.add_argument('--labels', type=Path)
    parser.add_argument('--output', type=Path, required=True); parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if args.execute: result = execute(args.output.resolve())
    else:
        if not all((args.old_zip, args.input, args.labels)): parser.error('准备协议需提供 --old-zip、--input、--labels')
        result = prepare(args.old_zip.resolve(), args.new_skill.resolve(), args.input.resolve(), args.labels.resolve(), args.output.resolve())
    print(json.dumps(result, ensure_ascii=False))
