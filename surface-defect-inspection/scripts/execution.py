"""跨阶段共享的请求账本与截止时间，不保存请求内容或凭据。"""
import math
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

_current = ContextVar('inspection_execution', default=None)
_item = ContextVar('inspection_item', default=None)


class ExecutionBudget:
    def __init__(self, max_api_calls=240, batch_seconds=1800, item_seconds=900):
        if type(max_api_calls) is not int or max_api_calls < 1:
            raise ValueError('max_api_calls必须为正整数')
        if any(not math.isfinite(v) or v <= 0 for v in (batch_seconds,item_seconds)):
            raise ValueError('deadline必须为正的有限秒数')
        self.max_api_calls=max_api_calls; self.batch_seconds=batch_seconds; self.item_seconds=item_seconds
        self.started=time.monotonic(); self.lock=threading.Lock(); self.calls=0; self.items={}
        self.usage={'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}; self.unknown=0

    def _remaining(self, item):
        now=time.monotonic()
        state=self.items.setdefault(item,{'started':now,'calls':0})
        return min(self.batch_seconds-(now-self.started),self.item_seconds-(now-state['started']))

    def remaining(self,item):
        with self.lock:return self._remaining(item)

    def reserve_request(self,item,requested_timeout):
        with self.lock:
            remaining=self._remaining(item)
            if remaining <= 0: raise RuntimeError('物体或批次总截止时间已到，停止新请求')
            if self.calls >= self.max_api_calls: raise RuntimeError('批次API请求预算已耗尽，停止新请求')
            self.calls+=1; self.items[item]['calls']+=1; self.unknown+=1
            return min(requested_timeout,remaining)

    def record_response(self,usage):
        if usage is None:return
        with self.lock:
            self.unknown=max(0,self.unknown-1)
            for key in self.usage:self.usage[key]+=getattr(usage,key,0) or 0

    def check(self,item):
        if self.remaining(item)<=0:raise RuntimeError('物体或批次总截止时间已到')

    def snapshot(self):
        with self.lock:
            return {'api_calls':self.calls,'max_api_calls':self.max_api_calls,
                    'request_unit':'cli_invocations' if getattr(self,'backend','deepseek')=='codex' else 'http_requests',
                    'batch_deadline_seconds':self.batch_seconds,'item_deadline_seconds':self.item_seconds,
                    'elapsed_seconds':round(time.monotonic()-self.started,3),'known_usage':dict(self.usage),
                    'requests_with_unknown_usage':self.unknown}


def current_execution():return _current.get()
def current_item():return _item.get()


@contextmanager
def execution_scope(budget,item):
    first=_current.set(budget);second=_item.set(item)
    try:yield
    finally:
        _item.reset(second);_current.reset(first)
