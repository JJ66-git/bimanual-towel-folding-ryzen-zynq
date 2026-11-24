#!/usr/bin/env python3
"""Local read-only frontend bridge to authenticated board snapshots. Never sends task packets."""
import argparse,json,mimetypes,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse,unquote
import psutil
from jupyter_client import JupyterBoard

ROOT=Path(__file__).resolve().parents[2];WEB=Path(__file__).with_name('web')
class Feed:
 def __init__(self,fixture=None):
  self.lock=threading.Lock();self.stop=threading.Event();self.snapshot=None;self.png=None;self.updated=None;self.error=None;self.fixture=fixture;self.cpu=None;self.last_board_tick=None
  self.board=None;self.profile=json.loads(Path(__file__).with_name('active_profile.json').read_text())
  try:self.ready=json.loads((ROOT/'npu_deploy/artifacts/readiness/latest.json').read_text())['software_checks']
  except (OSError,ValueError,KeyError):self.ready={}
  self.worker=threading.Thread(target=self.poll,daemon=True);self.worker.start()
 def poll(self):
  last_image=0.;psutil.cpu_percent()
  while not self.stop.is_set():
   try:
    if self.fixture:
     value=json.loads(self.fixture.read_text());image=self.fixture.with_suffix('.png');png=image.read_bytes() if image.exists() else None
    else:
     if self.board is None:self.board=JupyterBoard()
     prefix=self.profile['board_root'].split('/jupyter_notebooks/',1)[1]+'/runs/'
     response=self.board.request('GET','/api/contents/'+prefix+'lcd_latest.json',params={'format':'text'},timeout=3).json()
     value=json.loads(response['content']);png=None
     if value.get('schema')!='cooperative_display_v1':raise RuntimeError('板端图形界面尚未更新，等待部署。')
     if time.monotonic()-last_image>1:
      import base64
      response=self.board.request('GET','/api/contents/'+prefix+'lcd_latest.png',params={'format':'base64'},timeout=3).json();png=base64.b64decode(response['content']);last_image=time.monotonic()
    cpu=psutil.cpu_percent()
    with self.lock:
     self.snapshot=value;self.error=None;self.cpu=cpu
     if self.fixture or value['snapshot_monotonic_ns']!=self.last_board_tick:
      self.updated=time.monotonic();self.last_board_tick=value.get('snapshot_monotonic_ns')
     if png:self.png=png
   except Exception as error:
    if not self.fixture:self.board=None
    with self.lock:self.error='板端读取失败：请检查网络或 Jupyter 登录。' if not isinstance(error,RuntimeError) else str(error)
   self.stop.wait(.5)
 def payload(self):
  with self.lock:
   age=None if self.updated is None else (time.monotonic()-self.updated)*1000
   return {'connected':age is not None and age<2500 and self.error is None,'transport_age_ms':age,'snapshot':self.snapshot,'error':self.error,
     'fixture':bool(self.fixture),'host_platform':{'cpu_percent':self.cpu,'rocm_ready':self.ready.get('rocm_device',{}).get('passed',False),'npu_ready':self.ready.get('npu_encoder_execution',{}).get('passed',False)}}

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=8765);parser.add_argument('--fixture',type=Path,help='Explicit simulation snapshot for browser verification only');args=parser.parse_args()
 feed=Feed(args.fixture)
 class Handler(BaseHTTPRequestHandler):
  def do_GET(self):
   if self.headers.get('Host') not in ('127.0.0.1:'+str(args.port),'localhost:'+str(args.port)):
    self.send_error(403);return
   route=unquote(urlparse(self.path).path)
   if route=='/api/state':data=json.dumps(feed.payload(),ensure_ascii=False).encode();mime='application/json; charset=utf-8'
   elif route=='/api/lcd.png':
    with feed.lock:data=feed.png
    if data is None:self.send_error(503);return
    mime='image/png'
   else:
    target=(WEB/('index.html' if route=='/' else route.lstrip('/'))).resolve()
    if WEB.resolve() not in target.parents or not target.is_file() or target.suffix not in ('.html','.css','.js','.woff2','.otf'):
     self.send_error(404);return
    data=target.read_bytes();mime=mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
   self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.end_headers();self.wfile.write(data)
  def log_message(self,*args):pass
 server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
 print(('SIMULATION_FIXTURE' if args.fixture else 'READ_ONLY_BOARD_FEED')+' http://127.0.0.1:'+str(args.port),flush=True)
 try:server.serve_forever()
 except KeyboardInterrupt:pass
 finally:feed.stop.set();server.server_close();feed.worker.join(timeout=4)
if __name__=='__main__':main()
