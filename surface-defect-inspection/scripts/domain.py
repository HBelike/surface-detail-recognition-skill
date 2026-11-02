"""模型输出契约与跨字段验证。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Category = Literal['linear_trace', 'material_loss']
State = Literal['present', 'not_observed', 'uncertain']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Finding(StrictModel):
    category: Category
    image_id: str
    bbox: list[float] = Field(min_length=4, max_length=4)
    description: str = Field(min_length=1)
    certainty: Literal['confirmed', 'uncertain']

    @model_validator(mode='after')
    def validate_box(self):
        x1, y1, x2, y2 = self.bbox
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError('bbox 必须为 [0,1] 范围内正面积的 xyxy 坐标')
        return self


class Observation(StrictModel):
    image_id: str
    quality: Literal['adequate', 'limited']
    note: str = Field(min_length=1)


class Inspection(StrictModel):
    linear_trace: State
    material_loss: State
    findings: list[Finding]
    observations: list[Observation] = Field(min_length=1)
    limitations: list[str]

    @model_validator(mode='after')
    def validate_evidence(self):
        ids = [o.image_id for o in self.observations]
        if len(ids) != len(set(ids)):
            raise ValueError('observations 不允许重复 image_id')
        for f in self.findings:
            if f.image_id not in ids:
                raise ValueError('发现项引用了未观察图像')
        for category in ('linear_trace', 'material_loss'):
            confirmed = any(f.category == category and f.certainty == 'confirmed' for f in self.findings)
            state = getattr(self, category)
            if (state == 'present') != confirmed:
                raise ValueError('present 必须且只能对应至少一条 confirmed 证据')
            if state == 'not_observed' and any(f.category == category for f in self.findings):
                raise ValueError('not_observed 不应包含该类候选证据')
            if state == 'not_observed' and all(o.quality == 'limited' for o in self.observations):
                raise ValueError('全部图像观察受限时不能直接判为未发现')
        return self


def validate_image_ids(result: Inspection, expected_ids: set[str]):
    if {o.image_id for o in result.observations} != expected_ids:
        raise ValueError('必须覆盖全部输入图像，且不能引用不存在的图像')


class Assessment(StrictModel):
    decision: State
    basis: Literal['localized_irregular_mark','surface_material_discontinuity','normal_structure',
                   'regular_processing_texture','reflection_or_shadow','no_visible_target','insufficient_detail','conflicting_evidence']
    image_ids: list[str] = Field(min_length=1)
    evidence_summary: str = Field(min_length=1)
    alternative_explanation: str = Field(min_length=1)
    resolved_evidence_ids: list[str] = Field(default_factory=list)


class Verification(StrictModel):
    inspection: Inspection
    assessments: dict[Category, Assessment]

    @model_validator(mode='after')
    def validate_decisions(self):
        if set(self.assessments)!={'linear_trace','material_loss'}:
            raise ValueError('核验必须分别覆盖两类')
        for category,assessment in self.assessments.items():
            if assessment.decision!=getattr(self.inspection,category):
                raise ValueError('核验结论与inspection不一致')
            if assessment.decision=='present':
                expected='localized_irregular_mark' if category=='linear_trace' else 'surface_material_discontinuity'
                if assessment.basis!=expected:raise ValueError('阳性必须对应可见形态依据，不能用正常结构或反光确证')
            if assessment.decision=='not_observed' and assessment.basis in ('insufficient_detail','conflicting_evidence'):
                raise ValueError('证据不足或冲突不能记为未发现')
        return self

    def validate_references(self,expected_ids):
        for assessment in self.assessments.values():
            if not set(assessment.image_ids)<=expected_ids:raise ValueError('核验依据引用不存在的图像')
