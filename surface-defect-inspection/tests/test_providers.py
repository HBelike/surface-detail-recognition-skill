"""验证供应商参数隔离、图像合同、凭据选择和真实响应留档边界。"""
import json
import os
import sys
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'evals'))
from provider_config import PROVIDERS, get_provider
from execution import ExecutionBudget, execution_scope
from vision_client import analyze
from compare_models import schedule, score_trials, select_finalists, protocol, ITEMS, ORDER


class ProviderTests(unittest.TestCase):
    def test_provider_parameters_are_not_cross_contaminated(self):
        qwen = PROVIDERS['qwen'].parameters()
        self.assertEqual(qwen['extra_body'], {'enable_thinking': True, 'vl_high_resolution_images': True})
        self.assertNotIn('reasoning_effort', qwen)
        self.assertNotIn('thinking', qwen['extra_body'])
        kimi = PROVIDERS['kimi'].parameters()
        self.assertNotIn('reasoning_effort', kimi)
        with self.assertRaises(ValueError): PROVIDERS['kimi'].parameters('disabled')

    def test_qwen_uses_own_key_and_never_stores_reasoning(self):
        provider = PROVIDERS['qwen']; budget = ExecutionBudget()
        budget.provider_config = provider; budget.capture_responses = True
        result = {'linear_trace': 'not_observed', 'material_loss': 'not_observed', 'findings': [],
                  'observations': [{'image_id': 'a', 'quality': 'adequate', 'note': '可观察'}], 'limitations': []}
        response = SimpleNamespace(model=provider.model, id='response-test', usage=None,
                                   choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=json.dumps(result), reasoning_content='禁止留档的内部推理'))])
        client = MagicMock(); client.chat.completions.create.return_value = response
        with patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'test-qwen-key'}, clear=True), patch('vision_client.OpenAI', return_value=client) as constructor, execution_scope(budget, 'a'):
            parsed, metadata = analyze([{'image_id': 'a', 'data_url': 'data:image/jpeg;base64,test'}], provider.model, provider.base_url, 1, 'enabled')
        self.assertEqual(constructor.call_args.kwargs['api_key'], 'test-qwen-key')
        self.assertNotIn('禁止留档的内部推理', json.dumps(metadata, ensure_ascii=False))
        self.assertNotIn('test-qwen-key', json.dumps(metadata))
        self.assertEqual(json.loads(metadata['responses'][0]['final_content']), result)
        self.assertEqual(budget.snapshot()['api_calls'], 1)
        self.assertEqual(parsed.linear_trace, 'not_observed')
        image = client.chat.completions.create.call_args.kwargs['messages'][1]['content'][-1]
        self.assertNotIn('detail', image['image_url'])

    def test_kimi31_uses_its_documented_completion_budget(self):
        parameters = get_provider('kimi', 'kimi-k3.1-pro').parameters('enabled', 'high')
        self.assertEqual(parameters, {'max_completion_tokens': 16384, 'reasoning_effort': 'high'})
        self.assertNotIn('max_tokens', parameters)
        self.assertNotIn('extra_body', parameters)

    def test_schedule_keeps_every_case_and_balances_order(self):
        blocks = schedule(ORDER)
        self.assertEqual(len(blocks), 18)
        self.assertEqual(sum(len(b['providers']) for b in blocks), 54)
        self.assertTrue(all(set(b['providers']) == set(ORDER) for b in blocks))
        self.assertEqual([sum(b['providers'][0] == n for b in blocks) for n in ORDER], [6, 6, 6])

    def test_missing_failed_and_uncertain_never_leave_denominator(self):
        labels = {'source': 'human', 'labels': [{'item_id': i, 'reviewed': True, 'linear_trace': 'present', 'material_loss': 'not_observed'} for i in ITEMS]}
        trial = {'provider': 'qwen', 'round': 1, 'item_id': ITEMS[0], 'record': {'item_id': ITEMS[0], 'status': 'completed', 'result': {'linear_trace': 'uncertain', 'material_loss': 'not_observed'}},
                 'execution': {'api_calls': 1, 'requests_with_unknown_usage': 0, 'known_usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}, 'wall_seconds': 1}
        score = score_trials([trial], labels, ('qwen',))['qwen']
        self.assertEqual((score['category_correct'], score['category_total']), (1, 36))
        self.assertEqual(score['confusion']['linear_trace']['FN'], 18)
        self.assertEqual(score['confusion']['linear_trace']['uncertain'], 1)
        self.assertEqual(score['confusion']['linear_trace']['failed_or_missing'], 17)
        self.assertFalse(score['gate_pass'])

    def test_changed_human_labels_require_a_new_protocol(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for item in ITEMS:
                (root / 'images' / item).mkdir(parents=True)
                Image.new('RGB', (8, 8), 'white').save(root / 'images' / item / 'view.png')
            labels = {'source': 'human', 'labels': [{'item_id': item, 'reviewed': True, 'linear_trace': 'present', 'material_loss': 'not_observed'} for item in ITEMS]}
            path = root / 'labels.json'; path.write_text(json.dumps(labels))
            with self.assertRaisesRegex(ValueError, '新协议'):
                protocol(root / 'images', path)


if __name__ == '__main__': unittest.main()
