"""兼容旧入口，改为有界证据流程；不再永久锁定首次明确判断。"""
import argparse
from pathlib import Path
from domain import Inspection, Verification
from evidence_pipeline import assemble, claim, register_result, run_evidence_review, process_one
from evidence_resolution import resolve_pair
from zoom_tools import plan_zoom


def review_entries(paths, baseline):
    return plan_zoom(paths, baseline)['entries']


def merge_review(baseline, review_result, entries, review_metadata):
    """旧函数仅做保守合并；冲突保留uncertain，完整入口负责一次裁决。"""
    before = Inspection.model_validate(baseline['result'])
    ledger = register_result(before, baseline['input_images'], 'overview') + register_result(review_result, entries, 'zoom')
    resolutions = {c: resolve_pair(claim(before, ledger, c, 'overview'), claim(review_result, ledger, c, 'zoom'), True)
                   for c in ('linear_trace', 'material_loss')}
    review = Verification.model_construct(inspection=review_result, assessments={})
    return assemble(baseline, [{'review': review, 'entries': entries, 'metadata': review_metadata}],
                    {c: d.state for c, d in resolutions.items()},
                    {'version': 2, 'ledger': ledger, 'resolutions': {c: d.to_dict() for c, d in resolutions.items()},
                     'semantic_stages': ['overview', 'zoom'], 'limitations': ['兼容合并未执行冲突裁决；分歧保持不确定。']})


def refine_one(baseline, paths, output, model, base_url, timeout, thinking, force, execution=None):
    return process_one(baseline, paths, output, model, base_url, timeout, thinking, force, execution)


def run_refinement(*args, **kwargs):
    return run_evidence_review(*args, **kwargs)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True); p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True); p.add_argument('--workers', type=int, default=3)
    p.add_argument('--timeout', type=float, default=180); p.add_argument('--thinking', choices=['enabled', 'disabled'], default='enabled')
    p.add_argument('--force', action='store_true'); a = p.parse_args()
    raise SystemExit(run_refinement(a.input, a.baseline, a.output, a.workers, timeout=a.timeout, thinking=a.thinking, force=a.force))
