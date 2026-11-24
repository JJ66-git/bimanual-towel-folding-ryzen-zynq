"""Nonblocking UI bridge to the existing local booth backend. No robot APIs."""
from collections import deque
import copy
import json
import threading
import time
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError

BUILD_ID=0x46504735


def http_json(path,payload=None):
    data=None if payload is None else json.dumps(payload).encode()
    headers={'Content-Type':'application/json','X-FlexiFold-Action':'record' if path.startswith('/capture/') else 'check'}
    request=Request('http://127.0.0.1:8766'+path,data=data,headers=headers)
    try:
        with build_opener(ProxyHandler({})).open(request,timeout=2) as response:return json.loads(response.read())
    except HTTPError as error:
        try:detail=json.loads(error.read()).get('error',str(error))
        except (ValueError,AttributeError):detail=str(error)
        raise RuntimeError(detail) from error


class InteractionBridge:
    def __init__(self,requester=http_json):
        self.request=requester;self.lock=threading.RLock();self.closed=threading.Event()
        self.pending=None;self.actions=deque();self.generation=0
        self.state=dict(ready=False,error='',result=None,requested_millirad=2650,
                        capture={},capture_busy=False,capture_error='')
        self.worker=threading.Thread(target=self._run,daemon=True)
    def start(self):self.worker.start()
    def snapshot(self):
        with self.lock:return copy.deepcopy(self.state)
    def angle(self,value):
        if type(value) is not int or not 1750<=value<=2650 or (value-1750)%10:raise ValueError('Invalid J2 slider value')
        with self.lock:
            self.generation+=1;self.pending=(self.generation,value)
            self.state.update(requested_millirad=value,result=None,error='')
    def preview_angle(self,value):
        with self.lock:
            self.generation+=1
            self.state.update(requested_millirad=value,result=None,error='')
            self.pending=None
    def capture_start(self,instruction):
        with self.lock:
            if (not self.state['ready'] or self.state['capture_busy']
                    or self.state['capture'].get('state')=='recording'):
                return
            self.state.update(capture_busy=True,capture_error='')
            self.actions.append(('/capture/start',dict(instruction=instruction,source='piper_leader_commands')))
    def capture_stop(self):
        with self.lock:
            episode=self.state.get('capture',{}).get('episode_id')
            if episode and not self.state['capture_busy']:
                self.state.update(capture_busy=True,capture_error='')
                self.actions.append(('/capture/stop',dict(episode_id=episode)))
    def _refresh(self):
        mode=self.request('/mode')
        ready=(mode.get('source')=='board' and mode.get('protocol')=='v3' and mode.get('expected_build_id')==BUILD_ID)
        capture=self.request('/capture/status')
        with self.lock:
            first=ready and not self.state['ready']
            self.state.update(ready=ready,mode=mode,capture=capture)
            if not ready:self.state['error']='等待匹配的 B35 实板互动服务'
            elif first:
                self.state['error']='';self.angle(self.state['requested_millirad'])
    def _check_angle(self,pending):
        generation,value=pending
        result=self.request('/check/angle',{'millirad':value})
        if (result.get('source')!='board' or result.get('build_id')!=BUILD_ID
                or result.get('reference_match') is not True or abs(result.get('value',-1)-value/1000)>1e-9):
            raise ValueError('回包来源、B35 身份或整数参考不匹配')
        with self.lock:
            if generation==self.generation:self.state.update(result=result,error='')
    def _capture_action(self,path,payload):
        try:
            if path=='/capture/start':
                mode=self.state['mode']
                if not mode.get('leader_enabled'):
                    raise RuntimeError(mode.get('leader_error') or '右主臂 CAN 接口尚未配置，请联系工作人员。')
                leader=self.request('/leader/status')
                if not leader.get('ready'):
                    raise RuntimeError(leader.get('error') or '未收到完整的主臂信号。请检查电源和 CAN 线后重试。')
            outcome=dict(capture=self.request(path,payload))
        except OSError:
            outcome=dict(capture_error='无法连接互动服务，请重新载入后重试。')
        except Exception as error:
            outcome=dict(capture_error=str(error))
        with self.lock:
            self.state.update(outcome,capture_busy=False)
    def _run(self):
        last_poll=0.
        while not self.closed.is_set():
            try:
                with self.lock:
                    action=self.actions.popleft() if self.actions else None
                if action:self._capture_action(*action)
                if time.monotonic()-last_poll>=.5:
                    self._refresh();last_poll=time.monotonic()
                with self.lock:
                    pending=self.pending if self.state['ready'] else None
                    if pending:self.pending=None
                if pending:self._check_angle(pending)
            except Exception as error:
                with self.lock:self.state.update(error=str(error),result=None)
            self.closed.wait(.05)
    def close(self):
        self.closed.set()
        if self.worker.is_alive():self.worker.join(timeout=.2)
