"""核验不等于多数票；测试语义约束、全视角覆盖与已知用量。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from domain import Inspection,Verification
from imaging import prepare_images
from view_detection import detect_views
from verify_evidence import verification_entries,_verify_one
from test_upgrade import baseline


def negative(entries):
    return Inspection(linear_trace='not_observed',material_loss='not_observed',findings=[],
                      observations=[{'image_id':e['image_id'],'quality':'adequate','note':'可观察'} for e in entries],limitations=[])


class VerificationTests(unittest.TestCase):
    def test_all_views_are_retained_for_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);paths=[]
            for i in range(5):
                path=root/f'v{i}.png';Image.new('RGB',(40,40),'white').save(path);paths.append(path)
            entries=verification_entries(paths,{'input_images':[],'result':{'findings':[]}})
            self.assertEqual({e['source'] for e in entries if e['image_id'].endswith('::full')},{p.name for p in paths})

    def test_normal_explanation_cannot_certify_positive(self):
        entries=[{'image_id':'x'}];data=negative(entries).model_dump();data['linear_trace']='present'
        data['findings']=[{'category':'linear_trace','image_id':'x','bbox':[.1,.1,.5,.5],'description':'划痕','certainty':'confirmed'}]
        assessments={c:{'decision':data[c],'basis':'regular_processing_texture' if c=='linear_trace' else 'no_visible_target',
                       'image_ids':['x'],'evidence_summary':'可见纹理','alternative_explanation':'加工纹'} for c in ('linear_trace','material_loss')}
        with self.assertRaises(ValueError):Verification(inspection=data,assessments=assessments)

    def test_missing_class_or_fake_reference_rejected(self):
        data=negative([{'image_id':'x'}])
        assessments={c:{'decision':'not_observed','basis':'no_visible_target','image_ids':['fake'],
                        'evidence_summary':'未见','alternative_explanation':'正常'} for c in ('linear_trace','material_loss')}
        result=Verification(inspection=data,assessments=assessments)
        with self.assertRaises(ValueError):result.validate_references({'x'})
        assessments.pop('material_loss')
        with self.assertRaises(ValueError):Verification(inspection=data,assessments=assessments)

    def test_single_clear_view_is_not_outvoted(self):
        with tempfile.TemporaryDirectory() as folder:
            paths=[]
            for i in range(3):
                path=Path(folder)/f'v{i}.png';Image.new('RGB',(40,40),'white').save(path);paths.append(path)
            count=0
            def model(entries,*args):
                nonlocal count
                result=negative(entries)
                if count==0:
                    d=result.model_dump();d['linear_trace']='present'
                    d['findings']=[{'category':'linear_trace','image_id':entries[0]['image_id'],'bbox':[.1,.1,.5,.5],'description':'可见划痕','certainty':'confirmed'}]
                    result=Inspection.model_validate(d)
                count+=1
                return result,{'usage':{'total_tokens':12},'request_count':1}
            with patch('view_detection.analyze',side_effect=model):
                result,meta,entries=detect_views(paths,'mock','http://example.invalid',1,'enabled')
            self.assertEqual(result.linear_trace,'present');self.assertEqual(meta['usage']['total_tokens'],36)
            self.assertEqual(meta['request_count'],3)

    def test_unexplained_negative_does_not_erase_prior_positive(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'view.png';Image.new('RGB',(40,40),'white').save(path)
            before=baseline(path);before['result']['linear_trace']='present'
            before['result']['findings']=[{'category':'linear_trace','image_id':'view.png::full','bbox':[.1,.1,.5,.5],'description':'可见划痕','certainty':'confirmed'}]
            before['evidence']=[{**before['result']['findings'][0],'source':'view.png','bbox_pixels':[4,4,20,20]}]
            entries=verification_entries([path],before)
            assessments={c:{'decision':'not_observed','basis':'no_visible_target','image_ids':[entries[0]['image_id']],
                            'evidence_summary':'本轮未见','alternative_explanation':'没有明确正常解释'} for c in ('linear_trace','material_loss')}
            review=Verification(inspection=negative(entries),assessments=assessments)
            with patch('evidence_pipeline.analyze',return_value=(review,{'usage':{'total_tokens':5}})):
                final,cached=_verify_one(before,[path],root,'mock','http://example.invalid',1,'enabled',False,None)
            self.assertEqual(final['result']['linear_trace'],'uncertain')
            self.assertEqual(final['refinement']['status'],'completed')
            self.assertEqual(final['result']['findings'][0]['certainty'],'uncertain')


if __name__=='__main__':unittest.main()
