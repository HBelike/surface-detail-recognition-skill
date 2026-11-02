"""通用OpenAI协议在隔离环境下通过本机HTTP服务验证，不调用云模型。"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from PIL import Image

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / 'scripts'))
from provider_config import get_provider
from inspect_images import fingerprint


class CompatibleHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.received.append({'path': self.path, 'body': body,
                                     'test_auth_valid': self.headers.get('Authorization') == 'Bearer local-test-credential'})
        ids = []
        for entry in body['messages'][1]['content']:
            if entry['type'] != 'text': continue
            try: data = json.loads(entry['text'])
            except ValueError: continue
            if isinstance(data, dict) and 'image_id' in data: ids.append(data['image_id'])
        result = {'linear_trace': 'not_observed', 'material_loss': 'not_observed', 'findings': [],
                  'observations': [{'image_id': i, 'quality': 'adequate', 'note': '本地协议夹具，非视觉真值'} for i in ids],
                  'limitations': ['只验证协议和本地执行链路']}
        payload = {'id': 'local-protocol-response', 'model': body['model'], 'object': 'chat.completion', 'created': 0,
                   'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(result)}}],
                   'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}}
        content = json.dumps(payload).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(content))); self.end_headers(); self.wfile.write(content)


class OpenAICompatibleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name) / '面试官 独立目录'
        images = self.root / 'images/obj'; images.mkdir(parents=True)
        for index in (1, 2): Image.new('RGB', (20, 20), 'white').save(images / f'view-{index}.png')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), CompatibleHandler); self.server.received = []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.env = {k: os.environ[k] for k in ('SYSTEMROOT', 'TEMP', 'TMP') if k in os.environ}
        self.env.update(OPENAI_API_KEY='local-test-credential', OPENAI_BASE_URL=f'http://127.0.0.1:{self.server.server_port}/v1',
                        OPENAI_MODEL='interviewer-vision-model', PYTHONUTF8='1', NO_PROXY='127.0.0.1,localhost')

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.temp.cleanup()

    def run_cli(self, script='inspect_images.py', extra=(), env=None):
        command = [sys.executable, '-X', 'utf8', str(SKILL / 'scripts' / script), '--input', str(self.root / 'images'),
                   '--output', str(self.root / 'results'), '--provider', 'openai', '--workers', '1', '--timeout', '3', *extra]
        return subprocess.run(command, cwd=self.root, env=self.env if env is None else env, capture_output=True,
                              text=True, encoding='utf-8', timeout=20)

    def test_cli_uses_standard_env_and_only_standard_request_fields(self):
        process = self.run_cli(); self.assertEqual(process.returncode, 0, process.stderr)
        request = self.server.received[0]; body = request['body']
        self.assertEqual(request['path'], '/v1/chat/completions'); self.assertTrue(request['test_auth_valid'])
        self.assertEqual(body['model'], 'interviewer-vision-model')
        self.assertEqual(body['response_format'], {'type': 'json_object'})
        self.assertEqual(body['max_completion_tokens'], 6000)
        self.assertEqual(set(body), {'model', 'messages', 'response_format', 'max_completion_tokens'})
        images = [c for c in body['messages'][1]['content'] if c['type'] == 'image_url']
        self.assertEqual(len(images), 2)
        self.assertTrue(all(i['image_url']['url'].startswith('data:image/jpeg;base64,') for i in images))
        summary = (self.root / 'results/summary.json').read_text(encoding='utf-8')
        self.assertNotIn('local-test-credential', summary)
        self.assertEqual(json.loads(summary)['records'][0]['provider']['name'], 'openai')

    def test_generic_full_entry_also_runs_with_only_interviewer_configuration(self):
        process = self.run_cli('run_skill.py'); self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue((self.root / 'results/report.html').exists())
        self.assertEqual(len(self.server.received), 1)

    def test_legacy_token_field_and_limit_changes_do_not_reuse_cache(self):
        process = self.run_cli(); self.assertEqual(process.returncode, 0, process.stderr)
        process = self.run_cli(); self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(len(self.server.received), 1)
        env = {**self.env, 'OPENAI_TOKEN_LIMIT_FIELD': 'max_tokens', 'OPENAI_MAX_OUTPUT_TOKENS': '4000'}
        process = self.run_cli(env=env); self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(len(self.server.received), 2)
        body = self.server.received[-1]['body']; self.assertEqual(body['max_tokens'], 4000)
        self.assertNotIn('max_completion_tokens', body)

    def test_missing_key_stops_before_http_and_doctor_stays_local(self):
        env = {k: v for k, v in self.env.items() if k != 'OPENAI_API_KEY'}
        env['DEEPSEEK_API_KEY'] = 'unrelated-local-test-credential'
        process = self.run_cli(env=env); self.assertEqual(process.returncode, 2)
        self.assertIn('OPENAI_API_KEY', process.stderr); self.assertEqual(self.server.received, [])
        process = self.run_cli(extra=('--doctor',), env=env); self.assertEqual(process.returncode, 0, process.stderr)
        self.assertFalse(json.loads(process.stdout)['network_tested']); self.assertEqual(self.server.received, [])

    def test_explicit_endpoint_and_model_override_environment(self):
        with patch.dict(os.environ, self.env, clear=True):
            provider = get_provider('openai', 'explicit-model', 'http://localhost:9999/v1')
        self.assertEqual((provider.model, provider.base_url), ('explicit-model', 'http://localhost:9999/v1'))
        self.assertEqual(provider.credential_env, 'OPENAI_API_KEY')

    def test_bad_configuration_is_rejected_without_echoing_values(self):
        with patch.dict(os.environ, {'OPENAI_BASE_URL': 'http://localhost/v1?api_key=private-query-value'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'OPENAI_MODEL'): get_provider('openai')
            with self.assertRaises(ValueError) as error: get_provider('openai', 'model')
            self.assertNotIn('private-query-value', str(error.exception))
        with patch.dict(os.environ, {'OPENAI_TOKEN_LIMIT_FIELD': 'vendor_option'}, clear=True):
            with self.assertRaises(ValueError): get_provider('openai', 'model')


if __name__ == '__main__': unittest.main()
