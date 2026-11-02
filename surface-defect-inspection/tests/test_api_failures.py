"""通过本地HTTP transport验证SDK重试边界，不访问网络。"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from openai import OpenAI
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from vision_client import analyze


class APIFailureTests(unittest.TestCase):
    def exercise(self,status=None,timeout=False):
        requests=[]
        def handler(request):
            requests.append(request)
            if timeout:
                raise httpx.ReadTimeout('模拟读取超时',request=request)
            return httpx.Response(status,json={'error':{'message':'不可回显的供应商原文','type':'test_error'}},request=request)
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
        client=OpenAI(api_key='unit-test-only',base_url='https://example.invalid',http_client=http_client,max_retries=2)
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'unit-test-only'}),patch('vision_client.OpenAI',return_value=client),patch('openai._base_client.time.sleep'):
            with self.assertRaises(RuntimeError) as error:
                analyze([{'image_id':'a','data_url':'data:image/jpeg;base64,test'}],'test','https://example.invalid',1)
        client.close()
        self.assertNotIn('不可回显的供应商原文',str(error.exception))
        return len(requests),str(error.exception)

    def test_rate_limit_is_retried_at_most_twice(self):
        count,error=self.exercise(status=429)
        self.assertEqual(count,3)
        self.assertIn('429',error)

    def test_authentication_error_is_not_retried(self):
        count,error=self.exercise(status=401)
        self.assertEqual(count,1)
        self.assertIn('401',error)

    def test_timeout_is_retried_at_most_twice(self):
        count,error=self.exercise(timeout=True)
        self.assertEqual(count,3)
        self.assertIn('APITimeoutError',error)


if __name__=='__main__':unittest.main()
