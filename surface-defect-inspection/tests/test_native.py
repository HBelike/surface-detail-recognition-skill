"""原生后端仅测试适配边界，mock不能证明模型视觉效果。"""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from native_client import native_schema,analyze_native
from domain import Verification
from execution import ExecutionBudget,execution_scope


class NativeTests(unittest.TestCase):
    def test_schema_has_fixed_categories(self):
        schema=native_schema(Verification)['properties']['assessments']
        self.assertEqual(set(schema['properties']),{'linear_trace','material_loss'})
        self.assertFalse(schema['additionalProperties'])

    def test_missing_cli_fails_before_inference(self):
        with patch('native_client.shutil.which',return_value=None):
            with self.assertRaises(RuntimeError):analyze_native([],'test',1)

    def test_success_is_structurally_validated_and_usage_saved(self):
        def process(command,**kwargs):
            target=Path(command[command.index('--output-last-message')+1])
            target.write_text(json.dumps({'linear_trace':'not_observed','material_loss':'not_observed','findings':[],
                 'observations':[{'image_id':'x','quality':'adequate','note':'mock'}],'limitations':[]}),encoding='utf-8')
            event={'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':2}}
            return SimpleNamespace(returncode=0,stdout=json.dumps(event),stderr='')
        budget=ExecutionBudget();budget.backend='codex'
        with patch('native_client.shutil.which',return_value='codex'),patch('native_client.subprocess.run',side_effect=process),execution_scope(budget,'a'):
            result,meta=analyze_native([{'image_id':'x','data_url':'data:image/jpeg;base64,dGVzdA=='}],'test',1)
        self.assertEqual(meta['usage']['total_tokens'],12)
        self.assertEqual(budget.snapshot()['api_calls'],1)
        self.assertEqual(budget.snapshot()['request_unit'],'cli_invocations')

    def test_tool_activity_disqualifies_output(self):
        response=SimpleNamespace(returncode=0,stdout=json.dumps({'type':'item.completed','item':{'type':'command_execution'}}),stderr='')
        with patch('native_client.shutil.which',return_value='codex'),patch('native_client.subprocess.run',return_value=response):
            with self.assertRaises(RuntimeError):analyze_native([],'test',1)


if __name__=='__main__':unittest.main()
