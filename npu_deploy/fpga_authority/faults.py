#!/usr/bin/env python3
"""Byte-level fault injection around the actual client and dispatch authority.

Reference mode uses the actual FoldSession protocol with a software guard.
Board mode sends diagnostics only; actuator callbacks append to an in-memory
list. Neither mode imports any camera, Piper, CAN or policy execution entrypoint.
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import socket
import sys
import time

PROJECT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PROJECT/'fpga_integration/fpga_code/fpga_workspace'))
from host import fold_protocol as wire
from host.fold_client import FoldClient
from host.action_authority import CheckedSender,AuthorityDenied
from host.protocol import Sample,RUNNING,UNKNOWN,CHECK_CORRECTION
from host.reference import load_profile,profile_crc,evaluate
from board.fold_bridge import FoldSession

CASES=('nominal','bounds','joint_step','tracking','gripper_ownership','correction_slew',
       'stale_feedback','unknown_feedback','expired_window','drop_request','bad_request_crc',
       'bad_reply_crc','old_sequence','wrong_session','wrong_build','wrong_reference',
       'late_reply','stop_during_wait','dispatch_error')


class VirtualClock:
    def __init__(self):self.now=1_000_000_000
    def __call__(self):return self.now
    def delay(self,seconds):self.now+=int(seconds*1e9)


def candidate(profile,step=0):
    pose=tuple(profile['start']);zero=(0,)*14
    return Sample(profile_crc(profile),step,RUNNING,(step+1)*33333,1000,0,pose,pose,pose,pose,zero,zero)


def reference_socket_factory(profile):
    crc=profile_crc(profile)
    class Guard:
        guard_crc=crc
        def check(self,sample,*args,**kwargs):
            ref=evaluate(sample,profile)
            return wire.FPG1_REPLY.pack(crc,ref['violations'],ref['events'],ref['phase'],1,0,4,15,wire.BUILD_ID,UNKNOWN)
    service=FoldSession('127.0.0.1',Guard(),profile)
    class Socket:
        def __init__(self,*args):self.responses=[]
        def connect(self,address):pass
        def settimeout(self,value):pass
        def close(self):pass
        def send(self,packet):
            try:self.responses.append(service.handle(packet,('127.0.0.1',32123)))
            except ValueError:pass
            return len(packet)
        def recv(self,size):
            if not self.responses:raise socket.timeout()
            return self.responses.pop(0)
    return Socket


class HookFactory:
    def __init__(self,factory,case,delay):
        self.factory=factory;self.case=case;self.delay=delay;self.task_requests=0;self.stop=False
    def __call__(self,*args):
        outer=self;inner=self.factory(*args)
        class Hook:
            pending=False
            def connect(self,address):return inner.connect(address)
            def settimeout(self,value):return inner.settimeout(value)
            def close(self):return inner.close()
            def send(self,packet):
                frame=wire.decode(packet);self.pending=frame.magic==b'FPG1' and frame.values[1]!=UNKNOWN
                if self.pending:
                    outer.task_requests+=1
                    if outer.case=='drop_request':return len(packet)
                    if outer.case=='bad_request_crc':packet=packet[:-1]+bytes((packet[-1]^1,))
                return inner.send(packet)
            def recv(self,size):
                if self.pending and outer.case=='late_reply':outer.delay(.025)
                packet=inner.recv(size)
                if not self.pending:return packet
                if outer.case=='stop_during_wait':outer.stop=True
                if outer.case=='bad_reply_crc':return packet[:-1]+bytes((packet[-1]^1,))
                frame=wire.decode(packet);values=list(frame.values);sequence=frame.sequence;session=frame.session
                if outer.case=='old_sequence':sequence=max(0,sequence-1)
                if outer.case=='wrong_session':session=1 if session!=1 else 2
                if outer.case=='wrong_build':values[8]^=1
                if outer.case=='wrong_reference':values[2]^=4
                return wire.encode(frame.magic,frame.version,frame.kind,session,sequence,frame.tick_us,values)
        return Hook()


def run_case(case,profile,*,real_board=False,budget_us=20000):
    clock=time.perf_counter_ns if real_board else VirtualClock()
    hooks=HookFactory(socket.socket if real_board else reference_socket_factory(profile),case,
        time.sleep if real_board else clock.delay)
    address=('192.168.2.99',9009) if real_board else ('127.0.0.1',9009)
    probe=replace(candidate(profile),step=UNKNOWN,flags=0,elapsed_us=0,feedback_age_us=UNKNOWN,queue_depth=UNKNOWN)
    until=time.monotonic()+8
    while True:
        client=FoldClient(address,profile_crc(profile),socket_factory=hooks,timeout=.2)
        try:client.probe(probe);break
        except (TimeoutError,ConnectionError):
            client.close()
            if not real_board or time.monotonic()>=until:raise
            time.sleep(.1)
    try:
        gate=CheckedSender(client,profile,reply_budget_us=budget_us,feedback_limit_us=100000,clock_ns=clock)
        sample=candidate(profile);values=list(sample.target)
        if case=='bounds':values[8]=2650000
        if case=='joint_step':values[0]+=profile['joint_step_limit']+1000
        if case in ('bounds','joint_step'):sample=replace(sample,target=tuple(values))
        if case=='tracking':
            values=list(sample.measured);values[0]+=profile['tracking_limit']+1000;sample=replace(sample,measured=tuple(values))
        if case=='gripper_ownership':values[6]+=1000;sample=replace(sample,target=tuple(values))
        if case=='correction_slew':
            values=list(sample.correction);values[0]=profile['correction_step_limit']+1000
            sample=replace(sample,flags=RUNNING|CHECK_CORRECTION,correction=tuple(values))
        if case=='stale_feedback':sample=replace(sample,feedback_age_us=100001)
        if case=='unknown_feedback':sample=replace(sample,feedback_age_us=UNKNOWN)
        sink=[];attempts=[]
        def dispatch(target):
            attempts.append(target)
            if case=='dispatch_error':raise OSError('injected ambiguous transport failure')
            sink.append(target)
        reason=None
        try:gate.send(sample,tuple(v/1e6 for v in sample.target),dispatch,
            deadline_ns=clock()-1 if case=='expired_window' else clock()+budget_us*1000,stop_requested=lambda:hooks.stop)
        except AuthorityDenied as error:reason=error.reason
        except OSError:
            if case!='dispatch_error':raise
            reason='dispatch_error'
        allowed=case=='nominal';expected_attempts=1 if allowed or case=='dispatch_error' else 0
        before=hooks.task_requests;latched=False
        if not allowed:
            try:gate.send(candidate(profile,1),tuple(v/1e6 for v in profile['start']),dispatch,
                deadline_ns=clock()+budget_us*1000)
            except AuthorityDenied as error:latched=error.reason.startswith('latched:')
        passed=(len(attempts)==expected_attempts and len(sink)==int(allowed) and
            (allowed or (reason is not None and latched and hooks.task_requests==before)))
        return dict(case=case,passed=passed,reason=reason,dispatch_attempts=len(attempts),accepted_callbacks=len(sink),
            rejected_callbacks=0 if len(attempts)==expected_attempts else len(attempts),latched=latched,
            requests_before_latched_retry=before,requests_after_latched_retry=hooks.task_requests,
            dispatch_outcome='unknown; never retried' if case=='dispatch_error' else 'known',records=list(gate.records))
    finally:client.close()


def run_matrix(*,real_board=False,budget_us=20000):
    profile=load_profile();rows=[]
    for case in CASES:
        try:row=run_case(case,profile,real_board=real_board,budget_us=budget_us)
        except Exception as error:row=dict(case=case,passed=False,error=type(error).__name__+': '+str(error))
        rows.append(row);print('AUTHORITY_FAULT='+json.dumps({k:v for k,v in row.items() if k!='records'}),flush=True)
    return dict(schema='fpga_authority_fault_matrix_v1',passed=all(r['passed'] for r in rows),cases=rows,
        real_pl=real_board and any(record.get('build_id')==wire.BUILD_ID for row in rows for record in row.get('records',[])),
        requested_real_pl=real_board,clock='observed' if real_board else 'virtual for injected delays',
        transport='real UDP + byte hooks' if real_board else 'actual FoldClient/FoldSession + in-memory transport',
        actuator='in_memory_only',robot_commands_sent=False,eligible_for_policy_execution=False)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();report=run_matrix();args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2)+'\n');return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
