#!/usr/bin/env python3
"""Drive clearly labeled SYNTHETIC task packets and prove real display state changes. No robot access."""
import argparse,base64,hashlib,json,sys,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'fpga_integration/fpga_code/fpga_workspace'))
from host.fold_client import FoldClient
from host.protocol import Sample,UNKNOWN
from host.reference import load_profile,profile_crc
from jupyter_client import JupyterBoard

class VisualRun:
 def __init__(self,out):self.out=out;out.mkdir(parents=True,exist_ok=True);self.samples=[];self.captures=[];self.stop=threading.Event();self.error=None
 def capture(self):
  b=JupyterBoard();prefix='fpga_fpg1_v3_b35_20261113/runs/';seen=set()
  while not self.stop.is_set():
   try:
    r=b.request('GET','/api/contents/'+prefix+'lcd_latest.json',params={'format':'text'}).json();s=json.loads(r['content'])
    if s['source']!='synthetic_test':raise RuntimeError('Display left SYNTHETIC mode')
    key=(s['phase'],s['verify'],s['stable'],s['tone'],bool(s['mask']))
    if key not in seen:
     image=b.request('GET','/api/contents/'+prefix+'lcd_latest.png',params={'format':'base64'}).json();data=base64.b64decode(image['content'])
     if hashlib.sha256(data).hexdigest()!=s.get('image_sha256'):continue
     name='%02d_phase%d_verify%d_%s'%(len(self.captures),s['phase'],s['verify'],s['tone'])
     (self.out/(name+'.json')).write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n');(self.out/(name+'.png')).write_bytes(data)
     self.captures.append({'name':name,'state':s});seen.add(key)
   except Exception as error:
    self.error=repr(error)
   self.stop.wait(.12)
 def drive(self):
  b=JupyterBoard();r=b.request('GET','/api/contents/fpga_fpg1_v3_b35_20261113/runs/lcd_latest.json',params={'format':'text'}).json();s=json.loads(r['content'])
  assert s['source']=='synthetic_test' and s['schema']=='cooperative_display_v1','Explicit SYNTHETIC service required'
  capture=threading.Thread(target=self.capture,daemon=True);capture.start()
  profile=load_profile();crc=profile_crc(profile);start=tuple(profile['start']);zeros=(0,)*14;client=FoldClient(('192.168.2.99',9009),crc,timeout=.7)
  step=0;began=time.monotonic_ns()
  def send(pose,target=None):
   nonlocal step
   sample=Sample(crc,step,4,(time.monotonic_ns()-began)//1000,12000,0,tuple(target or pose),tuple(pose),tuple(pose),tuple(pose),zeros,zeros)
   reply=client.send_sample(sample);assert reply is not None,'Missing matched reply'
   self.samples.append({'step':step,'phase':reply.values[5],'mask':reply.values[1],'verify':(reply.values[6]>>8)&3,'values':list(reply.values)})
   step+=1;return reply
  try:
   client.probe(Sample(crc,UNKNOWN,0,0,UNKNOWN,UNKNOWN,start,start,start,start,zeros,zeros));time.sleep(.6)
   opened=list(start);opened[6]=opened[13]=40000
   gripped=opened[:];gripped[6]=gripped[13]=6000
   folded=gripped[:];folded[1]=folded[8]=1730000;folded[2]=folded[9]=-896000
   retreat=folded[:];retreat[1]=retreat[8]=500000;retreat[2]=retreat[9]=-300000
   for pose,expected in [(opened,1),(gripped,2),(folded,3)]:
    for i in range(19):reply=send(pose);time.sleep(.075)
    assert reply.values[5]==expected and reply.values[1]==0,reply.values
   for i in range(4):reply=send(retreat);time.sleep(.06)
   event=reply.values[9];assert event!=UNKNOWN
   event_rx=time.monotonic_ns()//1000;features=[];refresh=time.monotonic()
   for i in range(3):
    deadline=event_rx/1e6+i*.25+.003
    while time.monotonic()<deadline:
     if time.monotonic()>=refresh:send(retreat);refresh=time.monotonic()+.06
     time.sleep(.002)
    send(retreat);refresh=time.monotonic()+.06
    result=client.send_feature(event,event_rx,i+1,time.monotonic_ns()//1000,{'area':429,'center':[320,240],'rectangularity':.9,'dark_fraction':.15},1000)
    assert result is not None;features.append(list(result.values))
   assert features==[[1,1,5,1],[2,1,5,2],[3,2,0,3]],features
   for i in range(16):send(retreat);time.sleep(.075)
   abnormal=retreat[:];abnormal[0]=profile['upper'][0]+1
   for i in range(14):reply=send(retreat,target=abnormal);assert reply.values[1]!=0;time.sleep(.075)
   time.sleep(.7)
  finally:
   client.close();self.stop.set();capture.join(timeout=4)
  stages={row['state']['phase'] for row in self.captures};states={row['state']['verify'] for row in self.captures};masks=[row['state']['mask'] for row in self.captures]
  record={'passed':{1,2,3}.issubset(stages) and 2 in states and any(m for m in masks if m is not None),'source':'SYNTHETIC','real_udp':True,'real_pl':True,'robot_commands_sent':False,
   'captured_stages':sorted(stages),'captured_verify_states':sorted(states),'capture_names':[row['name'] for row in self.captures],
   'features':features,'task_samples':self.samples,'last_capture_error':self.error}
  (self.out/'dynamic_visual_validation.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in record.items() if k!='task_samples'},ensure_ascii=False),flush=True)
  assert record['passed'],'Not all real UI transitions were captured'
  return record

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--outdir',type=Path,required=True);args=p.parse_args();VisualRun(args.outdir).drive()
if __name__=='__main__':main()
