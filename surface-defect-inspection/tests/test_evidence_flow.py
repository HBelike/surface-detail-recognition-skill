"""验证完整证据调用链的阶段上限、几何、用量和拒判，而非视觉效果。"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'evals'))
from domain import Inspection, Verification
from imaging import prepare_images, map_box
from evidence_pipeline import process_one
from zoom_tools import plan_zoom
from test_upgrade import baseline


def response(entries, positive=False, ids=None):
    inspection=Inspection(linear_trace='present' if positive else 'not_observed',material_loss='not_observed',
                          findings=([{'category':'linear_trace','image_id':entries[0]['image_id'],'bbox':[.1,.1,.3,.3],
                                      'description':'mock局部线','certainty':'confirmed'}] if positive else []),
                          observations=[{'image_id':e['image_id'],'quality':'adequate','note':'mock观察'} for e in entries],limitations=[])
    assessments={c:{'decision':getattr(inspection,c),'basis':'localized_irregular_mark' if positive and c=='linear_trace' else 'no_visible_target',
                    'image_ids':[e['image_id'] for e in entries],'evidence_summary':'mock证据','alternative_explanation':'mock替代解释',
                    'resolved_evidence_ids':ids or []} for c in ('linear_trace','material_loss')}
    return Verification(inspection=inspection,assessments=assessments)


class EvidenceFlowTests(unittest.TestCase):
    def test_contrary_positive_gets_one_adjudication_and_all_usage(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'view.png';Image.new('RGB',(400,400),'white').save(path)
            before=baseline(path);before['metadata']={'usage':{'total_tokens':11},'request_count':1}
            before['result']['material_loss']='uncertain'
            calls=[]
            def model(entries,*args,task=None,schema=None):
                calls.append(task)
                ids=task['conflicts']['linear_trace']['evidence_ids'] if task['phase']=='adjudication' else []
                return response(entries,True,ids),{'usage':{'total_tokens':7},'request_count':1}
            with patch('evidence_pipeline.analyze',side_effect=model):
                result,_=process_one(before,[path],root,'mock','http://example.invalid',1,'enabled',False)
            self.assertEqual(len(calls),2)
            self.assertEqual(result['evidence_flow']['semantic_stages'],['overview','zoom','adjudication'])
            self.assertEqual(result['result']['linear_trace'],'present')
            self.assertEqual(result['metadata']['usage']['total_tokens'],25)
            self.assertEqual(result['metadata']['request_count'],3)
            self.assertTrue(result['evidence_flow']['resolutions']['linear_trace']['adjudication_evidence_complete'])
            self.assertNotIn('data:image',json.dumps(result))

    def test_adjudication_cannot_resolve_without_both_sides(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'view.png';Image.new('RGB',(400,400),'white').save(path)
            before=baseline(path);before['result']['material_loss']='uncertain'
            def model(entries,*args,**kwargs):return response(entries,True),{'usage':{'total_tokens':7}}
            with patch('evidence_pipeline.analyze',side_effect=model) as call:
                result,_=process_one(before,[path],root,'mock','http://example.invalid',1,'enabled',False)
            self.assertEqual(call.call_count,2)
            self.assertEqual(result['result']['linear_trace'],'uncertain')
            self.assertTrue(all(f['certainty']=='uncertain' for f in result['result']['findings']))

    def test_adjudication_failure_is_failed_with_known_usage(self):
        from vision_client import AnalysisFailure
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'view.png';Image.new('RGB',(400,400),'white').save(path)
            before=baseline(path);before['metadata']={'usage':{'total_tokens':11},'request_count':1}
            before['result']['material_loss']='uncertain'
            def model(entries,*args,task=None,**kwargs):
                if task['phase']=='adjudication':raise AnalysisFailure('mock失败',{'usage':{'total_tokens':3},'request_count':1})
                return response(entries,True),{'usage':{'total_tokens':7},'request_count':1}
            with patch('evidence_pipeline.analyze',side_effect=model):
                result,_=process_one(before,[path],root,'mock','http://example.invalid',1,'enabled',False)
            self.assertEqual(result['refinement']['status'],'failed')
            self.assertEqual(result['result']['linear_trace'],'uncertain')
            self.assertEqual(result['metadata']['usage']['total_tokens'],21)

    def test_new_conflict_in_last_stage_is_not_locked_or_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'view.png';Image.new('RGB',(400,400),'white').save(path)
            before=baseline(path);before['result']['material_loss']='uncertain'
            def model(entries,*args,task=None,**kwargs):
                ids=task['conflicts']['linear_trace']['evidence_ids'] if task['phase']=='adjudication' else []
                value=response(entries,True,ids).model_dump()
                if task['phase']=='adjudication':
                    value['inspection']['material_loss']='present'
                    value['inspection']['findings'].append({'category':'material_loss','image_id':entries[0]['image_id'],'bbox':[.5,.5,.8,.8],'certainty':'confirmed','description':'mock新缺口'})
                    value['assessments']['material_loss'].update(decision='present',basis='surface_material_discontinuity')
                return Verification.model_validate(value),{'usage':{'total_tokens':7}}
            with patch('evidence_pipeline.analyze',side_effect=model) as call:
                final,_=process_one(before,[path],root,'mock','http://example.invalid',1,'enabled',False)
            self.assertEqual(call.call_count,2)
            self.assertEqual(final['result']['material_loss'],'uncertain')
            self.assertFalse(final['evidence_flow']['resolutions']['material_loss']['last_stage_change']['additional_call_allowed'])

    def test_all_candidates_registered_and_budget_skips_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            paths=[];root=Path(folder)
            for i in range(5):
                path=root/f'v{i}.png';Image.new('RGB',(1600,1600),'white').save(path);paths.append(path)
            entries=prepare_images(paths,'baseline');findings=[]
            for e in entries:
                for box in ([.05,.05,.1,.1],[.8,.8,.85,.85]):
                    findings.append({'category':'linear_trace','image_id':e['image_id'],'bbox':box,'certainty':'uncertain','description':'mock候选'})
            inspection=Inspection(linear_trace='uncertain',material_loss='not_observed',findings=findings,
                                  observations=[{'image_id':e['image_id'],'quality':'adequate','note':'mock'} for e in entries],limitations=[])
            plan=plan_zoom(paths,{'input_images':entries,'result':inspection.model_dump()})
            self.assertEqual(len(plan['evidence']),10)
            self.assertEqual(len(plan['requests']),6)
            self.assertEqual(plan['skipped_requests'],4)
            self.assertEqual(sum(e['zoom_status']=='skipped_budget' for e in plan['evidence']),4)
            self.assertEqual(len([e for e in plan['entries'] if e['image_id'].endswith('::full')]),5)
            self.assertEqual(len({r['image_id'] for r in plan['requests']}),5)
            for entry in plan['entries']:
                box=map_box([0,0,1,1],entry)
                self.assertEqual(box,entry['crop_box'])

    def test_localization_requires_same_source_and_category(self):
        from localization import evaluate_localization
        truth={'source':'human','annotations':[{'source_type':'human','item_id':'a','image_id':'a/x','source':'x',
                                                'category':'linear_trace','bbox_pixels':[0,0,20,20]}]}
        predicted={'records':[{'item_id':'a','status':'completed','evidence':[{'source':'y','category':'linear_trace','certainty':'confirmed','bbox_pixels':[0,0,20,20]}]}]}
        self.assertEqual(evaluate_localization(predicted,truth)['region_hit_rate'],0)
        predicted['records'][0]['evidence'][0]['source']='x'
        self.assertEqual(evaluate_localization(predicted,truth)['mean_iou'],1)
        self.assertEqual(evaluate_localization(predicted,{'source':'human','annotations':[]})['status'],'not_evaluated')

    def test_skipped_candidates_cannot_silently_become_negative(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'view.png';Image.new('RGB',(3000,3000),'white').save(path)
            before=baseline(path);before['result']['linear_trace']='uncertain'
            before['result']['findings']=[]
            for index in range(7):
                x=(index%3)*.3+.03;y=(index//3)*.3+.03
                before['result']['findings'].append({'category':'linear_trace','image_id':'view.png::full','bbox':[x,y,x+.03,y+.03],
                                                     'certainty':'uncertain','description':'mock候选'})
            entry=before['input_images'][0]
            before['evidence']=[{**f,'source':'view.png','bbox_pixels':map_box(f['bbox'],entry)} for f in before['result']['findings']]
            with patch('evidence_pipeline.analyze',side_effect=lambda entries,*args,**kw:(response(entries),{'usage':{'total_tokens':7}})):
                final,_=process_one(before,[path],root,'mock','http://example.invalid',1,'enabled',False)
            self.assertEqual(final['result']['linear_trace'],'uncertain')
            self.assertEqual(len(final['evidence_flow']['skipped_evidence_ids']),1)

    def test_single_configuration_has_no_fake_improvement_and_no_fake_usage(self):
        from aggregate_results import aggregate
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);run=root/'eval-0/old_skill/run-1';run.mkdir(parents=True)
            (run/'grading.json').write_text(json.dumps({'summary':{'passed':1,'failed':0,'total':1,'pass_rate':1},'expectations':[]}),encoding='utf-8')
            (run/'timing.json').write_text(json.dumps({'total_tokens':0,'total_duration_seconds':0,'duration_known':False,'usage_complete':False}),encoding='utf-8')
            benchmark=aggregate(root)
            self.assertNotIn('delta',benchmark['run_summary'])
            self.assertIsNone(benchmark['run_summary']['old_skill']['tokens'])
            self.assertIsNone(benchmark['run_summary']['old_skill']['time_seconds'])

    def test_reference_signature_changes_detection_cache(self):
        from inspect_images import fingerprint
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'view.png';Image.new('RGB',(40,40),'white').save(path)
            first=fingerprint([path],'baseline','mock','http://example.invalid',reference_signature={'hash':'a'})
            second=fingerprint([path],'baseline','mock','http://example.invalid',reference_signature={'hash':'b'})
            self.assertNotEqual(first,second)

    def test_normal_reference_cannot_reuse_identical_pixels_under_another_encoding(self):
        from reference_images import configure_references
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'test.png';normal=root/'normal.png'
            image=Image.new('RGB',(40,40),'white');image.save(path,compress_level=0);image.save(normal,compress_level=9)
            self.assertNotEqual(path.read_bytes(),normal.read_bytes())
            manifest=root/'refs.json'
            manifest.write_text(json.dumps({'source':'human','item_categories':{'test':'metal'},'references':[
                {'category':'metal','item_id':'other','confirmed_normal':True,'path':'normal.png','sha256':hashlib.sha256(normal.read_bytes()).hexdigest()}]}),encoding='utf-8')
            with self.assertRaises(ValueError):configure_references(manifest,{'test':[path]})

    def test_gate_does_not_accept_only_best_round(self):
        from assertions import grade_summary
        from run_benchmark import release_gate
        labels={'source':'human','labels':[{'item_id':str(i),'reviewed':True,'linear_trace':'not_observed','material_loss':'not_observed'} for i in range(6)]}
        summary={'records':[{'item_id':str(i),'status':'completed','result':{'linear_trace':'not_observed','material_loss':'not_observed'}} for i in range(6)]}
        perfect=grade_summary(summary,labels)
        bad=copy.deepcopy(summary);bad['records']=bad['records'][:4];bad=grade_summary(bad,labels)
        self.assertTrue(release_gate([perfect]*3,[perfect]*3)['passed'])
        self.assertFalse(release_gate([perfect]*3,[perfect,perfect,bad])['passed'])


if __name__=='__main__':unittest.main()
