"""局部复查只处理不确定类别，前后证据可审计且不会循环调用。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from domain import Inspection
from imaging import prepare_images
from inspect_images import validate_completed
from refine_uncertain import review_entries,merge_review,refine_one


class RefinementTests(unittest.TestCase):
    def baseline(self,path,uncertain=True):
        entries=prepare_images([path],'baseline')
        result=Inspection(linear_trace='uncertain' if uncertain else 'not_observed',material_loss='not_observed',findings=[],
            observations=[{'image_id':e['image_id'],'quality':'adequate','note':'已检查'} for e in entries],limitations=[])
        return {'item_id':'a','status':'completed','mode':'baseline','thinking':'enabled','result':result.model_dump(),
            'input_images':[{k:v for k,v in e.items() if k!='data_url'} for e in entries],'evidence':[],
            'metadata':{'usage':dict(prompt_tokens=10,completion_tokens=1,total_tokens=11)},'elapsed_seconds':1,'fingerprint':'baseline'}

    def test_review_retains_contrary_evidence_and_keeps_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'view.png';Image.new('RGB',(300,300),'white').save(path)
            baseline=self.baseline(path);entries=review_entries([path],baseline)
            result=Inspection(linear_trace='present',material_loss='present',findings=[
                {'category':c,'image_id':entries[0]['image_id'],'bbox':[.1,.1,.4,.4],'description':'局部证据','certainty':'confirmed'} for c in ['linear_trace','material_loss']],
                observations=[{'image_id':e['image_id'],'quality':'adequate','note':'局部复查'} for e in entries],limitations=[])
            final=merge_review(baseline,result,entries,{'usage':dict(prompt_tokens=20,completion_tokens=2,total_tokens=22)})
            self.assertEqual(final['result']['linear_trace'],'present')
            self.assertEqual(final['result']['material_loss'],'uncertain')
            self.assertTrue(final['evidence_flow']['resolutions']['material_loss']['needs_adjudication'])
            self.assertEqual(final['refinement']['baseline_result'],baseline['result'])
            self.assertEqual(final['metadata']['usage']['total_tokens'],33)
            validate_completed(final,[path])
            self.assertNotIn('data:image',json.dumps(final))

    def test_failed_review_preserves_uncertain_baseline(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'view.png';Image.new('RGB',(300,300),'white').save(path)
            baseline=self.baseline(path)
            with patch('evidence_pipeline.analyze',side_effect=RuntimeError('模拟复查失败')) as model:
                final,cached=refine_one(baseline,[path],root,'test','https://example.invalid',1,'enabled',False)
            self.assertEqual(model.call_count,1)
            self.assertEqual(final['result']['linear_trace'],'uncertain')
            self.assertEqual(final['refinement']['status'],'failed')
            validate_completed(final,[path])

    def test_clear_baseline_skips_model_and_reuses_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'view.png';Image.new('RGB',(300,300),'white').save(path)
            baseline=self.baseline(path,False)
            with patch('evidence_pipeline.analyze') as model:
                first,_=refine_one(baseline,[path],root,'test','https://example.invalid',1,'enabled',False)
                second,cached=refine_one(baseline,[path],root,'test','https://example.invalid',1,'enabled',False)
                model.assert_not_called()
            self.assertTrue(cached)
            self.assertEqual(first['refinement']['status'],'not_needed')


if __name__=='__main__':unittest.main()
