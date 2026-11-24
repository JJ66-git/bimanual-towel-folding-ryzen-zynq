"""Authenticated PYNQ Jupyter REST/kernel access. Credentials never enter logs."""
import argparse
import base64
import http.cookiejar
import json
import os
from pathlib import Path
import sys
import time
import uuid
from urllib.parse import urlparse

import requests

ROOT=Path(__file__).resolve().parents[2]
PRIVATE=ROOT/'npu_deploy/artifacts/fpga_board/private'
DEFAULT_URL='http://192.168.2.99:9090'


class JupyterBoard:
    def __init__(self,url=DEFAULT_URL):
        parsed=urlparse(url)
        if parsed.scheme not in ('http','https') or parsed.hostname!='192.168.2.99':
            raise ValueError('Only the explicitly selected local FPGA board is allowed')
        self.url=url.rstrip('/')
        PRIVATE.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(PRIVATE,0o700)
        self.cookies=http.cookiejar.MozillaCookieJar(str(PRIVATE/'jupyter_cookies.txt'))
        if Path(self.cookies.filename).exists():self.cookies.load(ignore_discard=True,ignore_expires=True)
        else:
            # A development checkout may reuse the same rig's existing login in
            # memory. Saving a new session still writes only this checkout.
            baseline=ROOT.parent/'baseline/source_files.json'
            if baseline.is_file():
                source=Path(json.loads(baseline.read_text())['source_workspace'])/ROOT.name/'npu_deploy/artifacts/fpga_board/private/jupyter_cookies.txt'
                if source.is_file():
                    original=http.cookiejar.MozillaCookieJar(str(source));original.load(ignore_discard=True,ignore_expires=True)
                    for cookie in original:self.cookies.set_cookie(cookie)
        self.session=requests.Session();self.session.trust_env=False;self.session.cookies=self.cookies

    def request(self,method,path,**kwargs):
        xsrf=next((c.value for c in self.cookies if c.name=='_xsrf'),None)
        headers=kwargs.pop('headers',{})
        if xsrf:headers['X-XSRFToken']=xsrf
        response=self.session.request(method,self.url+path,headers=headers,timeout=kwargs.pop('timeout',20),**kwargs)
        if urlparse(response.url).hostname!='192.168.2.99':raise RuntimeError('Unexpected board redirect')
        response.raise_for_status()
        return response

    def authenticated(self):
        try:
            response=self.request('GET','/api/contents',params={'content':0})
            return 'path' in response.json()
        except (requests.RequestException,ValueError):return False

    def login_dialog(self):
        if self.authenticated():print('JUPYTER_AUTH_READY: existing board session',flush=True);return
        sys.path.insert(0,str(ROOT/'amd_hybrid_deploy_20261031'))
        from shared_runtime.tk_support import load_tk
        tk=load_tk()
        from tkinter import simpledialog
        root=tk.Tk();root.withdraw();root.attributes('-topmost',True)
        password=simpledialog.askstring('FPGA Jupyter · 192.168.2.99',
            '请输入这台PYNQ板卡的Jupyter密码。\n仅用于本地板卡登录，不记录密码。',show='*',parent=root)
        root.destroy()
        if password is None:raise RuntimeError('Jupyter login cancelled')
        self.request('GET','/login')
        xsrf=next((c.value for c in self.cookies if c.name=='_xsrf'),None)
        response=self.request('POST','/login',data={'password':password,'_xsrf':xsrf},headers={'Referer':self.url+'/login'})
        password=None
        if not self.authenticated():raise RuntimeError('Jupyter login rejected; session was not saved')
        path=Path(self.cookies.filename)
        fd=os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600);os.close(fd)
        self.cookies.save(ignore_discard=True,ignore_expires=True);os.chmod(path,0o600)
        print('JUPYTER_AUTH_READY: authenticated local board; private session saved with mode 0600',flush=True)

    def upload(self,path,data):
        parts=path.strip('/').split('/')
        for index in range(1,len(parts)):
            directory='/'.join(parts[:index])
            try:self.request('GET','/api/contents/'+directory,params={'content':0})
            except requests.HTTPError as exc:
                if exc.response.status_code!=404:raise
                self.request('PUT','/api/contents/'+directory,json={'type':'directory'})
        self.request('PUT','/api/contents/'+path,json={'type':'file','format':'base64',
                     'content':base64.b64encode(data).decode('ascii')})

    def kernel(self):
        state=PRIVATE/'kernel.json'
        if state.exists():
            key=json.loads(state.read_text())['id']
            try:self.request('GET','/api/kernels/'+key);return key
            except requests.HTTPError as exc:
                if exc.response.status_code!=404:raise
        result=self.request('POST','/api/kernels',json={'name':'python3'}).json()
        state.write_text(json.dumps({'id':result['id']})+'\n');os.chmod(state,0o600)
        return result['id']

    def execute(self,code,timeout=120):
        import websocket
        key=self.kernel();session_id=uuid.uuid4().hex;msg_id=uuid.uuid4().hex
        cookies='; '.join(c.name+'='+c.value for c in self.cookies)
        url=self.url.replace('http://','ws://',1).replace('https://','wss://',1)
        connection=websocket.create_connection(url+'/api/kernels/'+key+'/channels?session_id='+session_id,
                         cookie=cookies,origin=self.url,timeout=timeout,http_proxy_host=None)
        message={'header':{'msg_id':msg_id,'username':'fpga_pc','session':session_id,'msg_type':'execute_request','version':'5.3'},
                 'parent_header':{},'metadata':{},'channel':'shell',
                 'content':{'code':code,'silent':False,'store_history':False,'user_expressions':{},'allow_stdin':False,'stop_on_error':True}}
        connection.send(json.dumps(message));outputs=[];errors=[];deadline=time.monotonic()+timeout
        try:
            while time.monotonic()<deadline:
                connection.settimeout(max(1,deadline-time.monotonic()))
                packet=json.loads(connection.recv())
                if packet.get('parent_header',{}).get('msg_id')!=msg_id:continue
                kind=packet.get('msg_type',packet.get('header',{}).get('msg_type'));content=packet.get('content',{})
                if kind=='stream':outputs.append(content.get('text',''))
                elif kind in ('execute_result','display_data'):outputs.append(content.get('data',{}).get('text/plain',''))
                elif kind=='error':errors.append({'name':content.get('ename'),'value':content.get('evalue'),'traceback':content.get('traceback',[])})
                elif kind=='status' and content.get('execution_state')=='idle':
                    result={'output':''.join(outputs),'errors':errors,'passed':not errors}
                    return result
            raise TimeoutError('Board Jupyter execution timed out')
        finally:connection.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--login',action='store_true')
    parser.add_argument('--code-file',type=Path)
    parser.add_argument('--out',type=Path)
    args=parser.parse_args();board=JupyterBoard()
    if args.login:board.login_dialog()
    if args.code_file:
        if not board.authenticated():raise RuntimeError('Authenticate using --login first')
        result=board.execute(args.code_file.read_text())
        if args.out:args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(result['output'])
        if result['errors']:print(json.dumps(result['errors'],ensure_ascii=False));return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
