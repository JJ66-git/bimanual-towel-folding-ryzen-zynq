#!/usr/bin/env python3
"""Real PC-to-board protocol validation with SYNTHETIC poses; no robot access."""
import argparse
import json
from pathlib import Path
import socket
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'fpga_integration/fpga_code/fpga_workspace'))
from host import fold_protocol as wire
from host.fold_client import FoldClient
from host.protocol import Sample,UNKNOWN,frame as old_frame
from host.reference import load_profile,profile_crc,evaluate


def run(address):
    profile=load_profile();crc=profile_crc(profile);start=tuple(profile['start']);zeros=(0,)*14
    clients=[];rows=[];timings=[]
    def make_probe():return Sample(crc,UNKNOWN,0,0,UNKNOWN,UNKNOWN,start,start,start,start,zeros,zeros)
    client=FoldClient(address,crc,timeout=.4);clients.append(client)
    try:
        probe=client.probe(make_probe())
        step=0;elapsed=0
        def send(pose):
            nonlocal step,elapsed
            sample=Sample(crc,step,4,elapsed,12000,0,pose,pose,pose,pose,zeros,zeros)
            begin=time.monotonic_ns();reply=client.send_sample(sample)
            rtt=(time.monotonic_ns()-begin)/1e6;timings.append(rtt)
            assert reply is not None,'no matched v3 reply'
            expected=evaluate(sample,profile)
            assert reply.values[1:4]==(expected['violations'],expected['events'],expected['phase']),reply.values
            assert reply.values[1]==0,reply.values
            rows.append({'step':step,'source':'SYNTHETIC','sequence':reply.sequence,'values':list(reply.values),'rtt_ms':rtt})
            step+=1;elapsed+=50000
            return reply
        opened=list(start);opened[6]=opened[13]=40000
        for _ in range(3):reply=send(tuple(opened))
        assert reply.values[5]==1
        gripped=opened[:];gripped[6]=gripped[13]=6000
        for _ in range(3):reply=send(tuple(gripped))
        assert reply.values[5]==2
        folded=gripped[:];folded[1]=folded[8]=1730000;folded[2]=folded[9]=-896000
        for _ in range(4):reply=send(tuple(folded))
        assert reply.values[5]==3
        retreat=folded[:];retreat[1]=retreat[8]=500000;retreat[2]=retreat[9]=-300000
        for _ in range(4):reply=send(tuple(retreat))
        event=reply.values[9];assert event!=UNKNOWN
        event_rx=time.monotonic_ns()//1000;features=[]
        for i in range(3):
            while time.monotonic_ns()//1000<event_rx+i*100000+1000:time.sleep(.001)
            send(tuple(retreat))
            result=client.send_feature(event,event_rx,i+1,time.monotonic_ns()//1000,
                     {'area':429,'center':[320,240],'rectangularity':.9,'dark_fraction':.15},1000)
            assert result is not None
            assert result.values[3]==i+1,result.values
            features.append(list(result.values))
        assert features[-1][1:]==[2,0,3],features
        terminal=client.query_event(event)
        assert terminal.values==(3,2,0,3),terminal.values
        # Old protocol and malformed packets must not receive a success reply.
        invalid=[]
        for name,packet in [('old_v1',old_frame(1,123,1,100,make_probe().pack())),
                            ('truncated',b'FPG1'),('bad_crc',(lambda p:p[:-1]+bytes([p[-1]^1]))(wire.encode(b'FVE1',1,3,client.session,event,123456,())))]:
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as test:
                test.connect(address);test.settimeout(.15);test.send(packet)
                try:test.recv(4096);raise AssertionError('Invalid packet accepted: '+name)
                except socket.timeout:invalid.append(name)
        # A genuinely new source socket/session may take over only after the lease.
        time.sleep(5.1)
        second=FoldClient(address,crc,timeout=.4);clients.append(second)
        second.probe(make_probe())
        fresh=Sample(crc,0,4,0,12000,0,start,start,start,start,zeros,zeros)
        response=second.send_sample(fresh);assert response is not None and response.values[5]==0
        return {'passed':True,'source':'SYNTHETIC','real_udp':True,'robot_commands_sent':False,
            'address':list(address),'fpg1_source':list(client.control.getsockname()),'fve1_source':list(client.vision.getsockname()),
            'build_id':hex(probe.values[8]),'profile_crc':hex(crc),'task_samples':rows,
            'feature_results':features,'query_result':list(terminal.values),'rejected_packets':invalid,
            'new_session_reset':True,'rtt_median_ms':statistics.median(timings),'rtt_max_ms':max(timings),
            'normative_release_ready':False,'qualification':'Network and PL subset validation; not field camera or robot success'}
    finally:
        for client in clients:client.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();result=run(('192.168.2.99',9009))
    args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='task_samples'},indent=2))

if __name__=='__main__':main()
