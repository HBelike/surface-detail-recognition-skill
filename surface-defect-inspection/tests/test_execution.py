"""验证模型空白重试和部分失败不会变成无缺陷。"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from domain import Inspection
from vision_client import analyze
from inspect_images import main, fingerprint


def valid_result():
    return Inspection.model_validate({'linear_trace':'not_observed','material_loss':'not_observed','findings':[],
        'observations':[{'image_id':'view.png::full','quality':'adequate','note':'可观察'}],'limitations':['仅限可见区域']})


class ExecutionTests(unittest.TestCase):
    def test_thinking_modes_do_not_share_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'view.png'
            Image.new('RGB',(20,20),'white').save(path)
            disabled=fingerprint([path],'crops','test','https://example.invalid','disabled')
            enabled=fingerprint([path],'crops','test','https://example.invalid','enabled')
            self.assertNotEqual(disabled,enabled)

    def test_thinking_mode_keeps_reasoning_out_of_results(self):
        response=SimpleNamespace(model='test',id='test',usage=None,
            choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content=valid_result().model_dump_json(),reasoning_content='不应保存在报告中的内部推理'))])
        mock_client=MagicMock()
        mock_client.chat.completions.create.return_value=response
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'unit-test-only'}),patch('vision_client.OpenAI',return_value=mock_client):
            result,meta=analyze([{'image_id':'view.png::full','data_url':'data:image/jpeg;base64,test'}],'test','https://example.invalid',1,'enabled')
        self.assertEqual(meta['thinking'],'enabled')
        self.assertNotIn('不应保存在报告中的内部推理',json.dumps([result.model_dump(),meta],ensure_ascii=False))
        args=mock_client.chat.completions.create.call_args.kwargs
        self.assertEqual(args['extra_body']['thinking']['type'],'enabled')

    def test_schema_error_is_actionable_without_echoing_input(self):
        bad=valid_result().model_dump()
        bad['observations'][0]['quality']='clear'
        bad['observations'][0]['note']='不应进入错误日志的内容'
        response=SimpleNamespace(model='test',id='test',usage=None,
            choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content=json.dumps(bad)))])
        mock_client=MagicMock()
        mock_client.chat.completions.create.return_value=response
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'unit-test-only'}),patch('vision_client.OpenAI',return_value=mock_client):
            with self.assertRaises(RuntimeError) as error:
                analyze([{'image_id':'view.png::full','data_url':'data:image/jpeg;base64,test'}],'test','https://example.invalid',1)
        self.assertIn('observations.0.quality',str(error.exception))
        self.assertNotIn('不应进入错误日志的内容',str(error.exception))
        self.assertEqual(mock_client.chat.completions.create.call_count,2)

    def test_blank_content_retried_and_usage_accumulated(self):
        def response(text):
            return SimpleNamespace(model='test',id='test',usage=SimpleNamespace(prompt_tokens=10,completion_tokens=2,total_tokens=12),
                choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content=text))])
        mock_client=MagicMock()
        mock_client.chat.completions.create.side_effect=[response('   '),response(valid_result().model_dump_json())]
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'unit-test-only'}),patch('vision_client.OpenAI',return_value=mock_client):
            result,meta=analyze([{'image_id':'view.png::full','data_url':'data:image/jpeg;base64,test'}],'test','https://example.invalid',1)
        self.assertEqual(meta['content_attempts'],2)
        self.assertEqual(meta['usage']['total_tokens'],24)
        self.assertEqual(result.linear_trace,'not_observed')

    def test_failed_group_does_not_stop_next_or_become_negative(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for item in ('a','b'):
                (root/'images'/item).mkdir(parents=True)
                Image.new('RGB',(20,20),'white').save(root/'images'/item/'view.png')
            argv=['inspect_images.py','--input',str(root/'images'),'--output',str(root/'output'),'--workers','1']
            with patch.object(sys,'argv',argv),patch.dict(os.environ,{'DEEPSEEK_API_KEY':'unit-test-only'}),patch('inspect_images.analyze',side_effect=[RuntimeError('测试模拟失败'),(valid_result(),{})]),contextlib.redirect_stdout(io.StringIO()):
                code=main()
            records=json.loads((root/'output'/'summary.json').read_text(encoding='utf-8'))['records']
            self.assertEqual(code,1)
            self.assertEqual([r['status'] for r in records],['failed','completed'])
            self.assertNotIn('result',records[0])


if __name__ == '__main__':
    unittest.main()
