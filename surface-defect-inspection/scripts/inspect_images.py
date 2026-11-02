"""按物体执行表面细节识别，支持缓存与失败后续跑。"""
import argparse
import hashlib
import json
import os
import sys
import time
import math
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from domain import Inspection, validate_image_ids
from imaging import discover, load_image, prepare_images, map_box
from vision_client import analyze, PROMPT
from execution import ExecutionBudget, execution_scope
from view_detection import detect_views
from provider_config import get_provider


def fingerprint(paths, mode, model, base_url, thinking='disabled',effort='high',backend='deepseek',reference_signature=None,provider_config=None):
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode('utf-8'))
        digest.update(path.read_bytes())
    digest.update(json.dumps([mode,model,base_url,thinking,effort,backend]).encode())
    digest.update(json.dumps(reference_signature or {},sort_keys=True).encode())
    digest.update(json.dumps(provider_config or {},sort_keys=True).encode())
    digest.update(PROMPT.read_bytes())
    # 预处理、映射和契约变化也必须使缓存失效。
    for name in ('inspect_images.py','imaging.py','domain.py','vision_client.py','execution.py','view_detection.py','native_client.py','reference_images.py','provider_config.py'):
        digest.update(Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.replace(path)


def item_path(output,item):
    # 保留旧常规路径；保留名使用独立目录，防止覆盖批次summary。
    return output/'items'/f'{item}.json' if item.casefold()=='summary' else output/f'{item}.json'


def render_report(output, records):
    lines = ['# 表面细节识别报告', '', '本报告为模型输出，未经人工确认。框为近似定位，证据条数不等于唯一缺陷数。', '']
    for r in records:
        lines.extend([f'## {r["item_id"]}', '', f'执行状态：{r["status"]}', ''])
        if r['status'] != 'completed':
            lines.extend([r['error'], ''])
            continue
        result = r['result']
        if r.get('refinement',{}).get('status')=='failed':
            lines.extend(['复查失败，保留初判；本物体完整流程未完成：'+r['refinement'].get('error','未知错误'),''])
        lines.extend([f'线状痕迹：{result["linear_trace"]}；局部缺损：{result["material_loss"]}', ''])
        for evidence in r['evidence']:
            lines.append(f'- {evidence["category"]} / {evidence["certainty"]}：{evidence["source"]}，原图框 {evidence["bbox_pixels"]}；{evidence["description"]}')
        for note in result['limitations']:
            lines.append(f'- 观察边界：{note}')
        lines.append('')
    (output/'report.md').write_text('\n'.join(lines),encoding='utf-8')


def validate_completed(record, paths):
    """缓存和报告都不能使用图像引用或坐标被破坏的成功记录。"""
    result = Inspection.model_validate(record['result'])
    entries = record['input_images']
    lookup = {entry['image_id']: entry for entry in entries}
    if len(lookup) != len(entries):
        raise ValueError('输入元数据包含重复image_id')
    validate_image_ids(result, set(lookup))
    sizes = {path.name: list(load_image(path).size) for path in paths}
    if {entry['source'] for entry in entries} != set(sizes):
        raise ValueError('输入元数据与当前原图不一致')
    for entry in entries:
        size = sizes[entry['source']]
        if entry['source_size'] != size:
            raise ValueError('输入尺寸与原图不一致')
        x1, y1, x2, y2 = entry['crop_box']
        if not all(type(v) is int for v in entry['crop_box']) or not (0 <= x1 < x2 <= size[0] and 0 <= y1 < y2 <= size[1]):
            raise ValueError('输入裁剪框越界')
    for name, size in sizes.items():
        full = lookup.get(f'{name}::full')
        if not full or full['crop_box'] != [0, 0, *size]:
            raise ValueError('缺少完整原图引用')
    expected = [{**f.model_dump(), 'source': lookup[f.image_id]['source'],
                 'bbox_pixels': map_box(f.bbox, lookup[f.image_id])} for f in result.findings]
    if record['evidence'] != expected:
        raise ValueError('原图证据与模型结果映射不一致')


def run_item(item, paths, args, output):
    started = time.monotonic()
    record = {'schema_version':'1.0','item_id':item,'status':'failed','mode':args.mode,
              'requested_model':args.model,'thinking':args.thinking,
              'backend':getattr(args,'backend','deepseek'),
              'timestamp':datetime.now(timezone.utc).isoformat(),
              'prompt_hash':hashlib.sha256(PROMPT.read_bytes()).hexdigest()}
    dest = item_path(output,item)
    try:
        budget=getattr(args,'execution',None)
        provider=getattr(budget,'provider_config',None)
        record['provider']=({'name':'codex','model':args.model} if getattr(args,'backend','deepseek')=='codex'
                            else provider.snapshot() if provider else get_provider('deepseek',args.model,args.base_url).snapshot())
        key = fingerprint(paths,args.mode,args.model,args.base_url,args.thinking,getattr(args,'reasoning_effort','high'),getattr(args,'backend','deepseek'),getattr(budget,'reference_snapshot',{}).get(item,{}),record['provider'])
        record['fingerprint'] = key
        if dest.exists() and not args.force:
            try:
                cached = json.loads(dest.read_text(encoding='utf-8'))
                if cached.get('item_id') == item and cached.get('fingerprint') == key and cached.get('status') == 'completed':
                    validate_completed(cached, paths)
                    return cached, True
            except (ValueError,KeyError,TypeError,IndexError,OSError):
                pass
        budget=getattr(args,'execution',None)
        if budget:budget.check(item)
        with execution_scope(budget,item):
            if args.mode=='per-view':
                result,metadata,entries=detect_views(paths,args.model,args.base_url,args.timeout,args.thinking)
            else:
                entries=prepare_images(paths,args.mode)
                result, metadata = analyze(entries,args.model,args.base_url,args.timeout,args.thinking)
        if budget:budget.check(item)
        lookup = {entry['image_id']:entry for entry in entries}
        evidence = [{**f.model_dump(),'source':lookup[f.image_id]['source'],
                     'bbox_pixels':map_box(f.bbox,lookup[f.image_id])} for f in result.findings]
        record.update(status='completed',result=result.model_dump(),evidence=evidence,metadata=metadata,
                      input_images=[{k:v for k,v in entry.items() if k!='data_url'} for entry in entries])
        validate_completed(record, paths)
    except Exception as exc:
        record['status'] = 'failed'
        # 失败记录不保留未经校验的识别状态，避免被下游当作阴性或阳性。
        for field in ('result','evidence','input_images'):
            record.pop(field, None)
        record['error'] = str(exc) if isinstance(exc,RuntimeError) else f'本地处理失败：{type(exc).__name__}'
        if hasattr(exc,'metadata'):record['metadata']=exc.metadata
    record['elapsed_seconds'] = round(time.monotonic()-started,3)
    atomic_json(dest,record)
    return record, False


def main(argv=None,execution=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=Path('results'))
    parser.add_argument('--items',nargs='+')
    parser.add_argument('--mode',choices=['baseline','crops','context','per-view'],default='baseline')
    parser.add_argument('--model')
    parser.add_argument('--backend',choices=['deepseek','codex'],default='deepseek')
    parser.add_argument('--provider',choices=['deepseek','qwen','kimi','openai'],default='deepseek')
    parser.add_argument('--base-url')
    parser.add_argument('--timeout',type=float,default=120)
    parser.add_argument('--thinking',choices=['disabled','enabled'],default='enabled')
    parser.add_argument('--reasoning-effort',choices=['high','max'],default='high')
    parser.add_argument('--workers',type=int,default=3,help='同时处理的物体数，范围1至8')
    parser.add_argument('--doctor',action='store_true')
    parser.add_argument('--force',action='store_true')
    parser.add_argument('--max-api-calls',type=int,default=240)
    parser.add_argument('--batch-deadline',type=float,default=1800)
    parser.add_argument('--item-deadline',type=float,default=900)
    args = parser.parse_args(argv)
    provider=get_provider(args.provider,args.model,args.base_url)
    args.model=args.model or ('gpt-6.1-sol' if args.backend=='codex' else provider.model)
    args.base_url=args.base_url or provider.base_url
    if args.backend=='codex' and args.provider!='deepseek':raise ValueError('codex backend不能与API provider混用')
    if not 1 <= args.workers <= 8:
        raise ValueError('--workers 必须为1至8')
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError('--timeout 必须为正的有限数值')
    groups = discover(args.input.resolve())
    selected = args.items or list(groups)
    if set(selected) - groups.keys():
        raise ValueError('指定了不存在的物体分组')
    if len(selected) != len(set(selected)):
        raise ValueError('--items 不允许重复物体')
    if args.doctor:
        count = 0
        for item in selected:
            for path in groups[item]:
                load_image(path)
                count += 1
        print(json.dumps({'groups':len(selected),'images':count,'key_configured':bool(os.getenv(provider.credential_env)), 'provider':provider.snapshot(),
                          'model':args.model,'backend':args.backend,'codex_cli_available':bool(shutil.which('codex')),'network_tested':False},ensure_ascii=False))
        return 0
    if args.backend=='deepseek' and not os.getenv(provider.credential_env):
        raise RuntimeError('请通过环境变量 '+provider.credential_env+' 配置凭据')
    args.execution=execution or ExecutionBudget(args.max_api_calls,args.batch_deadline,args.item_deadline)
    args.execution.reasoning_effort=args.reasoning_effort
    args.execution.backend=args.backend
    if args.backend!='codex':args.execution.provider_config=provider
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    records_by_id = {}
    batch_started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = {pool.submit(run_item,item,groups[item],args,output):item for item in selected}
        for job in as_completed(jobs):
            record, cached = job.result()
            if record['item_id']!=jobs[job]:raise ValueError('返回物体ID与提交任务不一致')
            records_by_id[record['item_id']] = record
            records = [records_by_id[item] for item in selected if item in records_by_id]
            atomic_json(output/'summary.json',{'schema_version':'1.0','expected_items':selected,
                'processed_groups':len(records),'wall_seconds':round(time.monotonic()-batch_started,3),
                'configuration':{'model':args.model,'mode':args.mode,'thinking':args.thinking,'workers':args.workers},
                'execution':args.execution.snapshot(),
                'records':records})
            render_report(output,records)
            print(json.dumps({'item_id':record['item_id'],'status':'cached' if cached else record['status'],
                'processed_groups':len(records),'expected_groups':len(selected),
                **({'error':record['error']} if 'error' in record else {})},ensure_ascii=False),flush=True)
    if set(records_by_id)!=set(selected):raise ValueError('批次结果物体集合不完整')
    return 1 if any(r['status']!='completed' for r in records) else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError,RuntimeError,OSError) as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(2)
