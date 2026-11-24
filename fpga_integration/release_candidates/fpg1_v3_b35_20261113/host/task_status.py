"""Sender-claimed task display status. This channel never grants motion permission."""
import argparse
import json
import math
from pathlib import Path
import secrets
import socket
import struct
import time
import zlib

HEADER = struct.Struct('<4sBBHIIQ')
CRC = struct.Struct('<I')
MAGIC = b'FUI1'
RESET_PHASES = ('RESETTING','RESET_DONE','RESET_FAILED','RESET_STOPPED')
PHASES = ('UNKNOWN','WAITING','GRASPING','GRASPED','FOLD_1','FOLD_2','COMPLETE','ENDED','FAILED','STOPPED') + RESET_PHASES
ACTIVITIES = ('IDLE','THINKING','EXECUTING','PAUSED','ERROR')
ORIGINS = ('HOST','OPERATOR')


def validate(values):
    if not isinstance(values,dict) or set(values) != {'phase','activity','origin'}:
        raise ValueError('task status requires phase, activity and origin only')
    for key,allowed in (('phase',PHASES),('activity',ACTIVITIES),('origin',ORIGINS)):
        if values[key] not in allowed:
            raise ValueError('invalid task '+key)
    return values


def pack(kind,session,sequence,tick,values):
    if kind not in (1,129):
        raise ValueError('invalid task packet kind')
    payload=json.dumps(validate(values),separators=(',',':'),ensure_ascii=True).encode('ascii')
    body=HEADER.pack(MAGIC,1,kind,len(payload),session,sequence,tick)+payload
    return body+CRC.pack(zlib.crc32(body)&0xffffffff)


def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError('duplicate task field')
        result[key]=value
    return result


def unpack(packet):
    if len(packet)<HEADER.size+CRC.size: raise ValueError('short task packet')
    magic,version,kind,size,session,sequence,tick=HEADER.unpack_from(packet)
    if magic!=MAGIC or version!=1 or kind not in (1,129) or not 0<size<=256:
        raise ValueError('invalid task header')
    if len(packet)!=HEADER.size+size+CRC.size: raise ValueError('task length mismatch')
    if zlib.crc32(packet[:-4])&0xffffffff != CRC.unpack_from(packet,len(packet)-4)[0]:
        raise ValueError('task CRC mismatch')
    try:
        values=json.loads(packet[HEADER.size:-4].decode('utf-8'),object_pairs_hook=unique_object)
    except (UnicodeError,json.JSONDecodeError) as error:
        raise ValueError('invalid task JSON') from error
    return kind,session,sequence,tick,validate(values)


class Client:
    def __init__(self,address,timeout=1.0,session=None):
        if not math.isfinite(timeout) or timeout<=0:
            raise ValueError('timeout must be finite and positive')
        self.socket=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        self.socket.connect(address)
        self.timeout=timeout
        if session is not None and (type(session) is not int or not 1<=session<=0xFFFFFFFE):
            self.socket.close();raise ValueError('invalid task session')
        self.session=secrets.randbelow(0xFFFFFFFE)+1 if session is None else session
        self.sequence=0
        self.tick=-1

    def send(self,phase,activity,origin):
        self.tick=max(self.tick+1,time.perf_counter_ns()//1000)
        values=dict(phase=phase,activity=activity,origin=origin)
        packet=pack(1,self.session,self.sequence,self.tick,values)
        expected=(129,self.session,self.sequence,self.tick,values)
        self.socket.send(packet)
        deadline=time.perf_counter()+self.timeout
        while True:
            remaining=deadline-time.perf_counter()
            if remaining<=0: raise TimeoutError('no matching task status acknowledgement')
            self.socket.settimeout(remaining)
            response=self.socket.recv(4096)
            try:
                if unpack(response)==expected: break
            except ValueError:
                continue
        self.sequence+=1
        return dict(session=self.session,sequence=self.sequence-1,**values)

    def close(self):
        self.socket.close()


class ResetClaims:
    """Separate display lease: never changes FPG ownership or a motion permit."""
    def __init__(self):
        self.owner=None;self.sequence=-1;self.tick=-1;self.last=0.;self.terminal=False

    def accept(self,identity,sequence,tick,values,address,now,*,task_active=False):
        owner=(address,identity)
        if values['phase'] not in RESET_PHASES or values['origin']!='HOST':raise ValueError('invalid_reset_claim')
        if task_active:raise ValueError('reset_display_task_active')
        if owner!=self.owner:
            if self.owner is not None and not self.terminal and now-self.last<5:raise ValueError('reset_display_lease_active')
            if sequence!=0 or values['phase']!='RESETTING':raise ValueError('reset_display_start_required')
        elif sequence<=self.sequence or tick<=self.tick or self.terminal:raise ValueError('old_reset_status')
        self.owner=owner;self.sequence=sequence;self.tick=tick;self.last=now
        self.terminal=values['phase']!='RESETTING'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--udp',required=True,metavar='HOST:PORT')
    parser.add_argument('--events',type=Path,required=True,help='JSONL with phase/activity/origin')
    parser.add_argument('--out',type=Path,required=True,help='new acknowledgement JSONL')
    args=parser.parse_args()
    rows=[validate(json.loads(line,object_pairs_hook=unique_object)) for line in args.events.read_text().splitlines() if line.strip()]
    if not rows: parser.error('task event file is empty')
    host,port=args.udp.rsplit(':',1)
    with args.out.open('x',encoding='utf-8') as output:
        client=Client((host,int(port)))
        try:
            for row in rows:
                result=client.send(**row)
                output.write(json.dumps(dict(event='task_acknowledged',robot_acknowledgement=False,**result))+'\n')
                output.flush()
                time.sleep(1)
        finally:
            client.close()


if __name__=='__main__': main()
