"""Coordinated synthetic task/PL/LCD demonstration. Never commands a robot."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import time

from host import task_status
from host import watchdog_protocol as wire
from host.protocol import RUNNING,Sample
from host.reference import load_profile,profile_crc
from host.watchdog_demo import Client,require


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--udp',required=True,metavar='HOST:PORT')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    host,port=args.udp.rsplit(':',1);address=(host,int(port))
    args.out.mkdir(parents=True,exist_ok=False)
    summary=dict(passed=False,input_source='synthetic_test',robot_commands=False)
    with (args.out/'events.jsonl').open('x',encoding='utf-8') as events, (args.out/'task_events.jsonl').open('x',encoding='utf-8') as task_events:
        client=Client(address,1.0,events);tasks=task_status.Client(address)
        def task(phase,activity):
            row=tasks.send(phase,activity,'HOST')
            task_events.write(json.dumps(dict(input_source='synthetic_test',robot_acknowledgement=False,**row))+'\n')
            task_events.flush()
        try:
            result=client.exchange('begin',wire.BEGIN,wire.MODE.pack(wire.ON_RESULT))
            require(result['status']==0,'ON_RESULT did not start disabled')
            task('WAITING','THINKING');time.sleep(1.1)
            result=client.exchange('thinking',wire.STATUS)
            require(result['status']==0 and result['timeout_count']==0,'thinking armed a periodic watchdog')
            profile=load_profile();pose=tuple(profile['start'])
            sample=Sample(profile_crc(profile),0,RUNNING,0,0xffffffff,0xffffffff,pose,pose,pose,pose,(0,)*14,(0,)*14)
            for step,phase in enumerate(('GRASPING','GRASPED','FOLD_1','FOLD_2')):
                task(phase,'EXECUTING')
                result=client.exchange(phase.lower(),wire.ALLOW,replace(sample,step=step).pack())
                require(result['violations']==0 and result['status']==0,'unexpected synthetic check result')
                time.sleep(1.1)
            task('COMPLETE','IDLE');time.sleep(1.1)
            result=client.exchange('end',wire.END)
            require(result['status']==0,'END left monitoring armed')
            summary['passed']=True
        finally:
            tasks.close();client.sock.close()
            summary['exchanges']=len(client.records)
            (args.out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    print('PASS: synthetic collaboration; no robot commands')


if __name__=='__main__':main()
