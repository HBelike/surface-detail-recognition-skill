"""独立验收发现的缺陷与证据核验改造回归。"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from domain import Inspection
from imaging import discover, prepare_images
from inspect_images import main
from refine_uncertain import refine_one
from build_report import build
from vision_client import analyze


def baseline(path):
    entries = prepare_images([path], 'baseline')
    result = Inspection(linear_trace='not_observed', material_loss='not_observed', findings=[],
                        observations=[{'image_id': e['image_id'], 'quality': 'adequate', 'note': '可见表面'} for e in entries], limitations=[])
    return {'item_id': 'a', 'status': 'completed', 'mode': 'baseline', 'result': result.model_dump(),
            'input_images': [{k:v for k,v in e.items() if k != 'data_url'} for e in entries],
            'evidence': [], 'metadata': {'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}},
            'elapsed_seconds': 0, 'fingerprint': 'baseline'}


class UpgradeTests(unittest.TestCase):
    def test_final_cache_rejects_wrong_item_id(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root/'view.png'; Image.new('RGB', (30,30), 'white').save(path)
            first, _ = refine_one(baseline(path), [path], root, 'test', 'https://example.invalid', 1, 'enabled', False)
            first['item_id'] = 'alien'
            (root/'a.json').write_text(json.dumps(first), encoding='utf-8')
            actual, cached = refine_one(baseline(path), [path], root, 'test', 'https://example.invalid', 1, 'enabled', False)
            self.assertEqual(actual['item_id'], 'a')
            self.assertFalse(cached)

    def test_root_child_collision_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/root.name).mkdir()
            for path in (root/'root.png', root/root.name/'child.png'):
                Image.new('RGB', (20,20), 'white').save(path)
            with self.assertRaises(ValueError): discover(root)

    def test_summary_named_item_has_separate_record(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); images=root/'images'; (images/'summary').mkdir(parents=True)
            path=images/'summary/view.png'; Image.new('RGB',(30,30),'white').save(path)
            result=Inspection.model_validate(baseline(path)['result'])
            argv=['inspect','--input',str(images),'--output',str(root/'out')]
            with patch.object(sys,'argv',argv),patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test-only'}),patch('inspect_images.analyze',return_value=(result,{})),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(),0)
            all_json=[json.loads(p.read_text(encoding='utf-8')) for p in (root/'out').rglob('*.json')]
            self.assertTrue(any(r.get('item_id')=='summary' for r in all_json))
            self.assertTrue((root/'out/summary.json').exists())

    def test_html_exposes_review_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'images/a').mkdir(parents=True)
            path=root/'images/a/view.png';Image.new('RGB',(30,30),'white').save(path)
            record=baseline(path);record['refinement']={'status':'failed','error':'mock 401'}
            summary=root/'summary.json';summary.write_text(json.dumps({'records':[record],'expected_items':['a']}),encoding='utf-8')
            build(root/'images',summary,root/'report.html')
            page=(root/'report.html').read_text(encoding='utf-8')
            self.assertIn('mock 401',page)
            self.assertIn('data-failed="1"',page)
            self.assertNotIn('执行完成 1 组，失败 0 组',page)

    def test_failed_content_preserves_usage(self):
        response=SimpleNamespace(model='mock',id='mock',usage=SimpleNamespace(prompt_tokens=10,completion_tokens=2,total_tokens=12),
                                 choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content='{}'))])
        client=MagicMock();client.chat.completions.create.return_value=response
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test-only'}),patch('vision_client.OpenAI',return_value=client):
            with self.assertRaises(RuntimeError) as error:
                analyze([{'image_id':'x','data_url':'data:image/jpeg;base64,test'}],'mock','https://example.invalid',1)
        self.assertEqual(getattr(error.exception,'metadata',{}).get('usage',{}).get('total_tokens'),24)

    def test_global_request_limit_applies_across_stages(self):
        from execution import ExecutionBudget, execution_scope, current_execution
        budget=ExecutionBudget(max_api_calls=1,batch_seconds=10,item_seconds=10)
        with execution_scope(budget,'a'):
            current_execution().reserve_request('a',1)
        with execution_scope(budget,'b'):
            with self.assertRaises(RuntimeError): current_execution().reserve_request('b',1)
        self.assertEqual(budget.snapshot()['api_calls'],1)

    def test_expired_deadline_prevents_request(self):
        from execution import ExecutionBudget
        clock=MagicMock(side_effect=[0,2])
        with patch('execution.time.monotonic',clock):
            budget=ExecutionBudget(max_api_calls=5,batch_seconds=1,item_seconds=1)
            with self.assertRaises(RuntimeError): budget.reserve_request('a',10)


if __name__=='__main__':unittest.main()
