"""真实CLI与本地HTTP接口集成：跨阶段预算、核验失败、缓存和逐视角。"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from PIL import Image


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        verify='"assessments"' in body['messages'][0]['content']
        ids=[]
        for entry in body['messages'][1]['content']:
            if entry['type']=='text':
                try:
                    meta=json.loads(entry['text'])
                    if isinstance(meta,dict) and 'image_id' in meta:ids.append(meta['image_id'])
                except ValueError:pass
        with self.server.lock:self.server.calls+=1
        if verify and self.server.fail_review:
            self.send_response(401);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(b'{"error":{"message":"mock failure"}}');return
        inspection={'linear_trace':'not_observed','material_loss':'not_observed','findings':[],
                    'observations':[{'image_id':i,'quality':'adequate','note':'mock观察'} for i in ids],'limitations':['mock不是视觉真值']}
        result=inspection
        if verify:
            result={'inspection':inspection,'assessments':{c:{'decision':'not_observed','basis':'no_visible_target',
                    'image_ids':ids,'evidence_summary':'mock未见','alternative_explanation':'正常'} for c in ('linear_trace','material_loss')}}
        payload={'id':'mock','model':'local-mock','object':'chat.completion','created':0,
                 'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result,ensure_ascii=False)}}],
                 'usage':{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12}}
        data=json.dumps(payload,ensure_ascii=False).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/'中文 空格';self.root.mkdir()
        for item in ('a','b'):
            folder=self.root/'images'/item;folder.mkdir(parents=True)
            for i in range(2):Image.new('RGB',(30,30),'white').save(folder/f'v{i}.png')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.calls=0;self.server.fail_review=False;self.server.lock=threading.Lock()
        threading.Thread(target=self.server.serve_forever,daemon=True).start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.temp.cleanup()

    def run_cli(self,*extra):
        script=Path(__file__).resolve().parents[1]/'scripts/run_skill.py'
        cmd=[sys.executable,'-X','utf8',str(script),'--input',str(self.root/'images'),'--output',str(self.root/'out'),
             '--base-url',f'http://127.0.0.1:{self.server.server_port}/v1','--model','local-mock','--workers','1',
             '--timeout','2','--batch-deadline','15','--item-deadline','15','--policy','verify',*extra]
        result=subprocess.run(cmd,cwd=self.root,env={**os.environ,'DEEPSEEK_API_KEY':'fake-local-only','PYTHONUTF8':'1','NO_PROXY':'127.0.0.1,localhost'},
                              capture_output=True,text=True,encoding='utf-8',timeout=25)
        self.assertTrue((self.root/'out/summary.json').exists(),result.stderr)
        summary=json.loads((self.root/'out/summary.json').read_text(encoding='utf-8'))
        return result,summary

    def test_full_pipeline_and_hot_cache(self):
        result,summary=self.run_cli()
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(summary['processed_groups'],2)
        self.assertEqual(self.server.calls,4);self.assertEqual(summary['execution']['api_calls'],4)
        result,summary=self.run_cli()
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(self.server.calls,4)
        self.assertEqual(summary['execution']['api_calls'],0)

    def test_one_call_budget_is_shared_across_both_stages(self):
        result,summary=self.run_cli('--max-api-calls','1')
        self.assertEqual(result.returncode,1);self.assertEqual(self.server.calls,1)
        self.assertEqual(summary['processed_groups'],2);self.assertEqual(summary['execution']['api_calls'],1)
        self.assertEqual(summary['execution']['known_usage']['total_tokens'],12)

    def test_review_failure_is_visible_in_html(self):
        self.server.fail_review=True
        result,summary=self.run_cli('--policy','verify')
        self.assertEqual(result.returncode,1)
        page=(self.root/'out/report.html').read_text(encoding='utf-8')
        self.assertIn('执行完成 0 组，失败 2 组',page)
        self.assertIn('401',page)
        self.assertTrue(all(r.get('refinement',{}).get('status')=='failed' for r in summary['records']))

    def test_per_view_requests_remain_accounted(self):
        result,summary=self.run_cli('--mode','per-view')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.server.calls,6);self.assertEqual(summary['execution']['api_calls'],6)
        self.assertEqual(summary['execution']['known_usage']['total_tokens'],72)

    def test_evidence_policy_skips_clear_well_observed_negatives(self):
        result,summary=self.run_cli('--policy','evidence')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.server.calls,2)
        self.assertTrue(all(r['refinement']['status']=='not_needed' for r in summary['records']))


if __name__=='__main__':unittest.main()
