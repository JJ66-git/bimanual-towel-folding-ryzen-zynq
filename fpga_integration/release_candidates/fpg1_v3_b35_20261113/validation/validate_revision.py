"""B35 real-PL defect regressions; synthetic samples only, no CAN or camera access."""
import json,secrets,time
from pathlib import Path
from host import fold_protocol as wire
from host.protocol import Sample,UNKNOWN
from host.reference import load_profile,profile_crc
from host.fold_config import load,identity
from board.fold_bridge import FoldPynqGuard,FoldSession

def run(ip,out):
 profile=load_profile();crc=profile_crc(profile);guard=FoldPynqGuard(ip,profile)
 config_identity=identity(load());assert ip.read(0x98)==config_identity['crc32']
 zeros=(0,)*14;start=tuple(profile['start']);rows=[];lease=[0.]
 session=FoldSession('192.168.2.10',guard,profile,clock=lambda:lease[0])
 identity_number=[secrets.randbelow(0x7ffffffe)+1];sequence=[0];step=[0];elapsed=[0]
 def send(sample,seq):
  packet=wire.encode(b'FPG1',3,1,identity_number[0],seq,time.monotonic_ns()//1000,wire.FPG1_REQUEST.unpack(sample.pack()))
  reply=wire.decode(session.handle(packet,('192.168.2.10',32002)))
  assert reply.values[1]==0,reply.values
  return reply
 def task(pose):
  sequence[0]+=1
  result=send(Sample(crc,step[0],4,elapsed[0],1000,0,pose,pose,pose,pose,zeros,zeros),sequence[0])
  step[0]+=1;elapsed[0]+=50000
  return result
 opened=list(start);opened[6]=opened[13]=40000
 gripped=opened[:];gripped[6]=gripped[13]=6000
 folded=gripped[:];folded[1]=folded[8]=1730000;folded[2]=folded[9]=-896000
 retreat=folded[:];retreat[1]=retreat[8]=500000;retreat[2]=retreat[9]=-300000
 def new_event():
  identity_number[0]+=1;lease[0]+=6;sequence[0]=step[0]=elapsed[0]=0
  send(Sample(crc,UNKNOWN,0,0,UNKNOWN,UNKNOWN,start,start,start,start,zeros,zeros),0)
  for pose,count in [(opened,3),(gripped,3),(folded,4),(retreat,4)]:
   for _ in range(count):reply=task(tuple(pose))
  event=reply.values[9];assert event!=UNKNOWN
  return event,time.monotonic_ns()//1000
 def feature(event,event_rx,frame_id):
  ready=time.monotonic_ns()//1000
  packet=wire.encode(b'FVE1',1,1,identity_number[0],event,ready,(event_rx,frame_id,4290,320,240,9000,1500,1))
  result=wire.decode(session.handle(packet,('192.168.2.10',32003)))
  rows.append({'frame_id':frame_id,'values':list(result.values),'raw_snapshot':hex(ip.read(0xcc))})
  return result.values
 event,event_rx=new_event()
 assert feature(event,event_rx,1)[1:]==(1,5,1)
 time.sleep(.23)
 stale=feature(event,event_rx,2)
 assert stale[1:]==(1,7,0),stale
 event,event_rx=new_event();next_refresh=time.monotonic()
 stable=[]
 for i in range(3):
  deadline=event_rx/1e6+i*.25+.002
  while time.monotonic()<deadline:
   if time.monotonic()>=next_refresh:
    task(tuple(retreat));next_refresh=time.monotonic()+.06
   time.sleep(.001)
  task(tuple(retreat));next_refresh=time.monotonic()+.06
  stable.append(feature(event,event_rx,i+1))
 assert stable==[(1,1,5,1),(2,1,5,2),(3,2,0,3)],stable
 assert ip.read(0x98)==config_identity['crc32']
 assert ip.read(0x4c)==0
 result={'passed':True,'real_pl':True,'source':'SYNTHETIC','build_id':hex(ip.read(0)),'phase_config':config_identity,
 'stale_joint_after_230ms':list(stale),'stable_frames_250ms_apart':[list(x) for x in stable],
 'packets':rows,'robot_commands_sent':False,'watchdog_control':ip.read(0x4c),'field_ready':False}
 out=Path(out);out.mkdir(parents=True,exist_ok=True);(out/'revision_real_pl.json').write_text(json.dumps(result,indent=2)+'\n')
 return result
