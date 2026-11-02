"""复用上游聚合算法，修正单配置虚假差值及缺失用量的展示。"""
import argparse
import importlib.util
import json
from pathlib import Path


def aggregate(root):
    root=Path(root)
    spec=importlib.util.spec_from_file_location('anthropic_aggregate',Path(__file__).parent/'vendor/anthropic/aggregate_benchmark.py')
    upstream=importlib.util.module_from_spec(spec);spec.loader.exec_module(upstream)
    benchmark=upstream.generate_benchmark(root,'surface-defect-inspection',str(Path(__file__).resolve().parents[1]))
    configs=[key for key in benchmark['run_summary'] if key!='delta']
    if not configs:raise ValueError('没有可聚合的评测运行')
    benchmark['metadata'].update(executor_model='以各轮原始结果中的response_model为准',analyzer_model='deterministic_python_assertions',
                                 runs_per_configuration={key:sum(r['configuration']==key for r in benchmark['runs']) for key in configs})
    if len(configs)<2:
        benchmark['run_summary'].pop('delta',None)
        benchmark['notes'].append('仅有一个配置，不能计算改进幅度或推断新版效果。')
    for config in configs:
        timing_files=list(root.glob(f'eval-*/{config}/run-*/timing.json'))
        timings=[json.loads(p.read_text(encoding='utf-8')) for p in timing_files]
        for field,key in [('tokens','usage_complete'),('time_seconds','duration_known')]:
            if any(not t.get(key,False) for t in timings):
                benchmark['run_summary'][config][field]=None
                benchmark['notes'].append(config+' 的 '+field+' 数据不完整，不展示为完整可比费用/耗时。')
                if 'delta' in benchmark['run_summary']:benchmark['run_summary']['delta'][field]=None
    lines=['# Skill评测聚合','', '组级两类都正确才算通过；拒判和失败保留在分母。', '', '| 配置 | 三轮整组通过率均值 | 标准差 | 最低 | 最高 |','|---|---:|---:|---:|---:|']
    for config in configs:
        rate=benchmark['run_summary'][config]['pass_rate']
        lines.append(f"| {config} | {rate['mean']:.2%} | {rate['stddev']:.2%} | {rate['min']:.2%} | {rate['max']:.2%} |")
    lines+=['','用量与耗时统计见benchmark.json；缺失字段不替换为字符数。']
    lines+=['- '+note for note in benchmark['notes']]
    (root/'benchmark.json').write_text(json.dumps(benchmark,ensure_ascii=False,indent=2),encoding='utf-8')
    (root/'benchmark.md').write_text('\n'.join(lines),encoding='utf-8')
    return benchmark


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('benchmark_dir',type=Path);a=p.parse_args()
    result=aggregate(a.benchmark_dir);print(json.dumps(result['run_summary'],ensure_ascii=False))
