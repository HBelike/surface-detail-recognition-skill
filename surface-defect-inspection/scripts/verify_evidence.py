"""兼容显式全组核验策略；复用区域预算与相反证据裁决。"""
from evidence_pipeline import process_one, run_evidence_review
from zoom_tools import plan_zoom


def verification_entries(paths, baseline):
    if 'observations' not in baseline['result']:
        from imaging import prepare_images
        return prepare_images(paths, 'context')
    return plan_zoom(paths, baseline)['entries']


def _verify_one(baseline, paths, output, model, base_url, timeout, thinking, force, execution):
    return process_one(baseline, paths, output, model, base_url, timeout, thinking, force, execution, review_all=True)


def run_verification(*args, **kwargs):
    return run_evidence_review(*args, **kwargs, review_all=True)
