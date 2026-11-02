"""只解决证据关系，不以模型置信度或票数替代人工真值。"""
from dataclasses import asdict, dataclass

STATES = ('present', 'not_observed', 'uncertain')


@dataclass(frozen=True)
class Claim:
    state: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self):
        if self.state not in STATES: raise ValueError('非法判断状态')


@dataclass(frozen=True)
class Resolution:
    state: str
    needs_adjudication: bool
    evidence_ids: tuple[str, ...]
    reason: str

    def to_dict(self): return asdict(self)


def resolve_pair(prior: Claim, review: Claim, same_region: bool) -> Resolution:
    ids = tuple(dict.fromkeys(prior.evidence_ids + review.evidence_ids))
    if prior.state == review.state:
        return Resolution(prior.state, False, ids, '两阶段结论一致；原图与裁剪不累计为独立投票')
    if prior.state == 'uncertain':
        return Resolution(review.state, False, ids, '对原先不确定结论补充观察')
    if prior.state == 'present' and not same_region:
        return Resolution('present', False, ids, '未覆盖原阳性区域的未见或不确定，不能推翻该阳性')
    if prior.state == 'not_observed' and review.state == 'uncertain':
        return Resolution('uncertain', False, ids, '新候选或观察不足不能被原阴性锁定')
    return Resolution('uncertain', True, ids, '相反证据或同一区域仍有分歧，最多进行一次针对性裁决')
