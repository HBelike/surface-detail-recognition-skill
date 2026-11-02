"""观察不足只能安全降级，不强制阴性或改写有效阳性。"""
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from domain import Inspection,Verification
from vision_client import validate_response


class SafeResponseTests(unittest.TestCase):
    def sample(self):
        return {'linear_trace':'present','material_loss':'not_observed',
                'findings':[{'category':'linear_trace','image_id':'x','bbox':[.1,.1,.5,.5],'certainty':'confirmed','description':'可见独立划痕'}],
                'observations':[{'image_id':'x','quality':'limited','note':'可见部分表面，阴性评估不足'}],'limitations':[]}

    def test_valid_positive_is_retained_when_negative_is_downgraded(self):
        result,repairs=validate_response(json.dumps(self.sample()),Inspection)
        self.assertEqual(result.linear_trace,'present');self.assertEqual(result.material_loss,'uncertain')
        self.assertEqual([r['category'] for r in repairs],['material_loss'])

    def test_original_contract_still_rejects_illegal_negative(self):
        with self.assertRaises(ValueError):Inspection.model_validate(self.sample())

    def test_verification_decision_changes_consistently(self):
        visual=self.sample()
        assessments={c:{'decision':visual[c],'basis':'localized_irregular_mark' if c=='linear_trace' else 'no_visible_target',
                         'image_ids':['x'],'evidence_summary':'可见事实','alternative_explanation':'不能排除局部细节'} for c in ('linear_trace','material_loss')}
        result,repairs=validate_response(json.dumps({'inspection':visual,'assessments':assessments}),Verification)
        self.assertEqual(result.inspection.material_loss,'uncertain')
        self.assertEqual(result.assessments['material_loss'].basis,'insufficient_detail')


if __name__=='__main__':unittest.main()
