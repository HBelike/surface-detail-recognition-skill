"""Skill入口：共享有界账本的检测、证据核验和HTML交付。"""
import argparse
import json
import math
import sys
from pathlib import Path
from execution import ExecutionBudget
from inspect_images import main as inspect_main,atomic_json,item_path
from refine_uncertain import run_refinement
from verify_evidence import run_verification
from build_report import build
from imaging import discover
from reference_images import configure_references
from provider_config import get_provider


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=Path('results'))
    parser.add_argument('--items',nargs='+')
    parser.add_argument('--workers',type=int,default=3)
    parser.add_argument('--timeout',type=float,default=180)
    parser.add_argument('--model')
    parser.add_argument('--backend',choices=['deepseek','codex'],default='deepseek')
    parser.add_argument('--provider',choices=['deepseek','qwen','kimi','openai'],default='deepseek')
    parser.add_argument('--base-url')
    parser.add_argument('--mode',choices=['baseline','context','crops','per-view'],default='baseline')
    parser.add_argument('--policy',choices=['evidence','verify','uncertain'],default='evidence')
    parser.add_argument('--references',type=Path,help='可选人工正常参照清单；默认零样本')
    parser.add_argument('--thinking',choices=['enabled','disabled'],default='enabled')
    parser.add_argument('--verification-thinking',choices=['enabled','disabled'],default='enabled')
    parser.add_argument('--reasoning-effort',choices=['high','max'],default='high')
    parser.add_argument('--max-api-calls',type=int,default=240)
    parser.add_argument('--batch-deadline',type=float,default=1800)
    parser.add_argument('--item-deadline',type=float,default=900)
    parser.add_argument('--baseline-summary',type=Path,help='显式复用完整初判作受控核验实验，不会自动当作同版本基线')
    parser.add_argument('--force',action='store_true')
    parser.add_argument('--doctor',action='store_true')
    args=parser.parse_args(argv)
    provider=get_provider(args.provider,args.model,args.base_url)
    args.model=args.model or ('gpt-6.1-sol' if args.backend=='codex' else provider.model)
    args.base_url=args.base_url or provider.base_url
    if args.backend=='codex' and args.provider!='deepseek':raise ValueError('codex backend不能与API provider混用')
    if not 1<=args.workers<=8:raise ValueError('--workers必须为1至8')
    if not math.isfinite(args.timeout) or args.timeout<=0:raise ValueError('--timeout必须为正有限数')
    budget=ExecutionBudget(args.max_api_calls,args.batch_deadline,args.item_deadline)
    budget.reasoning_effort=args.reasoning_effort
    budget.backend=args.backend
    if args.backend!='codex':budget.provider_config=provider
    root=args.input.resolve();output=args.output.resolve()
    command=['--input',str(root),'--output',str(output/'baseline'),'--workers',str(args.workers),
             '--timeout',str(args.timeout),'--model',args.model,'--base-url',args.base_url,
             '--mode',args.mode,'--thinking',args.thinking,'--reasoning-effort',args.reasoning_effort,'--backend',args.backend,'--provider',args.provider]
    if args.items:command+=['--items',*args.items]
    if args.force:command+=['--force']
    if args.doctor:
        result=inspect_main(command+['--doctor'],execution=budget)
        if args.references:
            _,snapshot=configure_references(args.references,discover(root))
            print(json.dumps({'reference_snapshot':snapshot},ensure_ascii=False))
        return result
    if args.references and (args.backend!='deepseek' or args.provider!='deepseek'):
        raise ValueError('可选正常参照当前仅接入DeepSeek适配；其他backend保持零样本')
    budget.references,budget.reference_snapshot=configure_references(args.references,discover(root))
    if args.baseline_summary:
        source=json.loads(args.baseline_summary.read_text(encoding='utf-8'))
        if args.items:
            selected=set(args.items)
            if selected-{r['item_id'] for r in source['records']}:raise ValueError('初判中缺少所选物体')
            source['records']=[r for r in source['records'] if r['item_id'] in selected]
            source['expected_items']=[r['item_id'] for r in source['records']]
        source['baseline_reuse_notice']='显式复用历史初判，初判模型/版本以原记录为准；不是本轮新检测。'
        atomic_json(output/'baseline/summary.json',source)
        for record in source['records']:atomic_json(item_path(output/'baseline',record['item_id']),record)
        code=1 if any(r['status']!='completed' for r in source['records']) else 0
    else:
        code=inspect_main(command,execution=budget)
    baseline=output/'baseline/summary.json'
    if not baseline.exists():return code or 1
    runner=run_verification if args.policy=='verify' else run_refinement
    review_code=runner(root,baseline,output,args.workers,args.model,args.base_url,args.timeout,
                       thinking=args.verification_thinking,force=args.force,execution=budget)
    summary=json.loads((output/'summary.json').read_text(encoding='utf-8'))
    summary['execution']=budget.snapshot()
    summary['configuration'].update(detection_mode=args.mode,detection_thinking=args.thinking,
                                    verification_thinking=args.verification_thinking,policy=args.policy,reasoning_effort=args.reasoning_effort,backend=args.backend,
                                    provider=provider.snapshot() if args.backend!='codex' else {'name':'codex','model':args.model})
    if args.baseline_summary:summary['baseline_reuse_notice']='显式复用历史初判，不能将历史检测耗时/usage计入本轮付费量。'
    atomic_json(output/'summary.json',summary)
    build(root,output/'summary.json',output/'report.html')
    return code or review_code


if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,RuntimeError,OSError) as exc:
        print(str(exc),file=sys.stderr);raise SystemExit(2)
