#!/usr/bin/env python3
"""Real PL + FAS1 + native desktop/LCD acceptance with an in-memory sink only."""
import argparse
from dataclasses import replace
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import signal
import time
from urllib.parse import quote

from benchmark import PROJECT,WORKSPACE,connect,sample,display_check,restore_service
sys.path.insert(0,str(PROJECT/'amd_hybrid_deploy_20261031'))
from host.action_authority import CheckedSender,AuthorityDenied
from host.action_status_publisher import Publisher
from host import action_status as wire
from host.reference import load_profile
from operation_lock_path import operation_lock
from shared_runtime.dashboard_view import DashboardState,render_dashboard,authority_projection,diagnostic_payload
from shared_runtime.dashboard_telemetry import ControlSnapshot,AuthoritySnapshot,BoardSnapshot,BoardLink


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    from jupyter_client import JupyterBoard
    from control_jupyter import request,PROFILE
    board=JupyterBoard();active=json.loads(PROFILE.read_text());profile=load_profile();address=('192.168.2.99',9009)
    report=dict(schema='fpga_dual_display_acceptance_v1',passed=False,cases=[],robot_commands_sent=False,
                physical_display_verified=False,eligible_for_policy_execution=False)
    clients=[];publishers=[];previous=None;changed=False
    prefix=str(Path(active['board_root']).relative_to('/home/xilinx/jupyter_notebooks'))
    endpoint='/files/'+quote(prefix+'/runs/',safe='/')
    def call(operation,**kwargs):return request(operation,board_client=board,**kwargs)
    def fresh():
        client=connect(address,profile);clients.append(client);publisher=Publisher(address,client.session,interval=.005);publishers.append(publisher)
        gate=CheckedSender(client,profile,reply_budget_us=20000,feedback_limit_us=100000)
        return client,publisher,gate
    def capture(name,client,publisher,row,gate=None):
        publisher.publish(row);deadline=time.monotonic()+3
        while publisher.delivered!=row:
            if time.monotonic()>deadline:raise RuntimeError('No correlated FAS1 receipt: '+str(publisher.error))
            time.sleep(.01)
        match=dict(session=client.session,authority=row)
        evidence=display_check(board,active,service['pid'],match,args.out/(name+'.lcd.png'))
        lcd=evidence['snapshot'];expected=wire.presentation(row)
        assert lcd['authority_presentation']['headline']==expected['headline'],(name,lcd['headline'])
        assert lcd['task_result']=='unconfirmed',name
        now=time.monotonic();desktop=DashboardState('REPLAY')
        control=ControlSnapshot('display-test-'+str(client.session),now,now,step=-1 if row['step']==wire.UNKNOWN else row['step'],max_actions=750,run_state='running',execute=False)
        desktop.ingest(dict(control=control,board_link=BoardLink(address,client.session,'diagnostic',750,750),authority=AuthoritySnapshot(now,client.session,row)))
        if gate and gate.last_reply:
            reply=gate.last_reply
            desktop.board=BoardSnapshot(now,client.session,reply.sequence,control.step,True,reply.values[1],round_trip_ms=row['round_trip_us']/1000 if row['round_trip_us']!=wire.UNKNOWN else 0,protocol_version=3,build_id=reply.values[8])
            desktop.board_history=(desktop.board,)
        assert authority_projection(desktop,now)['headline']==lcd['authority_presentation']['headline']
        for page in ('cameras','fpga'):
            image=render_dashboard(desktop,1520,940,now=now,page=page)[0];image.save(args.out/(name+'.pc-'+page+'.png'))
        (args.out/(name+'.json')).write_text(json.dumps(dict(lcd=lcd,desktop=diagnostic_payload(desktop)),ensure_ascii=False,indent=2)+'\n')
        report['cases'].append(dict(name=name,passed=True,session=client.session,target_sequence=row['target_sequence'],stage=row['stage'],headline=lcd['headline'],image_sha256=lcd['image_sha256'],framebuffer=lcd['framebuffer']))
        print('DUAL_DISPLAY_CASE='+name,flush=True)
    def row(stage,client,**kwargs):return wire.values(stage,mode='diagnostic',sink='memory',target_sequence=client.sequence-1,**kwargs)
    def send(gate,step=0,*,bad=False,dispatch=None):
        candidate=sample(profile,step)
        if bad:
            target=list(candidate.target);target[8]=2650000;candidate=replace(candidate,target=tuple(target))
        try:gate.send(candidate,tuple(value/1e6 for value in candidate.target),dispatch or sink.append,deadline_ns=time.perf_counter_ns()+20000000)
        except (AuthorityDenied,RuntimeError):pass
        record=dict(gate.records[-1]);stage='dispatched' if record.get('dispatched') else 'dispatch_unknown' if record['decision']=='DISPATCH_ERROR' else 'denied'
        return row(stage,gate.client,step=step,mask=record.get('violations',wire.UNKNOWN),feedback_age_us=candidate.feedback_age_us,
            round_trip_us=int(record.get('round_trip_us',wire.UNKNOWN)),reason='dispatch_error' if stage=='dispatch_unknown' else record.get('reason',''),
            sent_count=gate.dispatched,outcome='sent' if stage=='dispatched' else 'unknown' if stage=='dispatch_unknown' else 'not_sent')
    sink=[]
    with operation_lock(PROJECT):
        try:
            previous=call('status');changed=True
            path=PROJECT/'flexifold_booth_first_grasp/prepare_hardware.py'
            spec=importlib.util.spec_from_file_location('display_prepare',path);prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)
            prepare.deploy(board,active,call);call('ensure',source='synthetic_test',isolated_lcd=True,bulk_vectors=True)
            warm=connect(address,profile);warm.close();time.sleep(1)
            # Renderer initialization can be slow; no pending actor action depends on it.
            deadline=time.monotonic()+45
            while not board.request('GET',endpoint+'service_health.json').json().get('display_ready'):
                if time.monotonic()>deadline:raise TimeoutError('LCD worker did not become ready')
                time.sleep(.2)
            service=call('status');client,publisher,gate=fresh()
            capture('waiting',client,publisher,row('waiting',client))
            capture('checking',client,publisher,wire.values('checking',mode='diagnostic',sink='memory',target_sequence=1,step=0))
            accepted=send(gate);assert accepted['stage']=='dispatched'
            capture('memory_received',client,publisher,accepted,gate)
            denied=send(gate,1,bad=True);assert denied['stage']=='denied' and denied['mask']!=0
            capture('pl_denied',client,publisher,denied,gate)
            # A delayed old pass cannot erase the current denial even on the live socket.
            publisher.publish(dict(denied,stage='allowed',outcome='not_sent',reason='',mask=0))
            deadline=time.monotonic()+2
            while publisher.error is None and time.monotonic()<deadline:time.sleep(.02)
            assert publisher.error is not None,'Invalid old pass unexpectedly acknowledged'
            state=board.request('GET',endpoint+'lcd_latest.json').json()
            assert state['authority']==denied
            report['old_pass_rejected']=True
            capture('ended',client,publisher,dict(denied,stage='ended'),gate)
            publisher.close();client.close()
            client,publisher,gate=fresh()
            original=client.send_sample
            def drop_reply(candidate):original(candidate);return None
            client.send_sample=drop_reply
            timeout=send(gate);assert timeout['reason']=='no_matching_reply'
            capture('reply_lost',client,publisher,timeout,gate)
            publisher.close();client.close()
            client,publisher,gate=fresh()
            def uncertain(_):raise RuntimeError('Injected memory-sink callback exception')
            unknown=send(gate,dispatch=uncertain);assert unknown['stage']=='dispatch_unknown'
            capture('dispatch_unknown',client,publisher,unknown,gate)
            publisher.close();client.close()
            client,publisher,gate=fresh()
            capture('new_session_waiting',client,publisher,row('waiting',client))
            assert len(sink)==1,'Only the one permitted memory callback may execute'
            report['accepted_memory_callbacks']=len(sink);report['rejected_callbacks']=0
            # Stop the owned renderer, then verify the final page-flip registers.
            # B35 VRAM is write-only; its AXI read returns SLVERR (RTL tested).
            call('stop')
            png=board.request('GET',endpoint+'lcd_latest.png').content
            snapshot=board.request('GET',endpoint+'lcd_latest.json').json()
            assert hashlib.sha256(png).hexdigest()==snapshot['image_sha256']
            inner="""from pynq import Overlay
from board.fpg1_v3_server import DEFAULT_BITFILE
import json
overlay=Overlay(str(DEFAULT_BITFILE),download=False);lcd=overlay.lcd
assert lcd.read(0)==0x4c434431
status=int(lcd.read(8));page=(status>>1)&1
print('FRONT_BUFFER='+json.dumps(dict(page=page,generation=int(lcd.read(0x14)),enabled=bool(lcd.read(4)&1),status=status)))
"""
            code="import os,sys,subprocess\nenv=os.environ.copy();env['PYTHONPATH']=%r\nr=subprocess.run([sys.executable,'-c',%r],cwd=%r,env=env,capture_output=True,text=True,timeout=20)\nprint(r.stdout)\nif r.returncode:raise RuntimeError(r.stderr)\n"%(active['board_root'],inner,active['board_root'])
            result=board.execute(code);assert result['passed'],result['errors']
            actual=json.loads(next(line.split('=',1)[1] for line in result['output'].splitlines() if line.startswith('FRONT_BUFFER=')))
            assert actual['generation']==snapshot['framebuffer']['generation'] and actual['page']==snapshot['framebuffer']['front_page'] and actual['enabled'],'LCD page-flip acknowledgement differs from saved frame'
            report.update(framebuffer_ack_verified=True,front_buffer=actual,framebuffer_readback_supported=False,passed=True)
        except (Exception,KeyboardInterrupt) as error:report.update(passed=False,error=type(error).__name__+': '+str(error))
        finally:
            for publisher in publishers:publisher.close()
            for client in clients:client.close()
            if changed and previous:
                try:report.update(restore_service(call,previous,active['address']))
                except Exception as error:report.update(passed=False,restore_error=str(error))
    (args.out/'display_acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('DUAL_DISPLAY_ACCEPTANCE='+json.dumps(report,ensure_ascii=False),flush=True)
    return 0 if report['passed'] else 1

def interrupted(*_):raise KeyboardInterrupt

if __name__=='__main__':
    signal.signal(signal.SIGTERM,interrupted)
    raise SystemExit(main())
