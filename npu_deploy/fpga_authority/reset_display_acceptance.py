#!/usr/bin/env python3
"""Real B35 startup lease and reset display acceptance; no camera/CAN/robot APIs.

Reset claims are explicitly SYNTHETIC display tests, not real robot reset
reports. Restore the prior service mode even after an interruption.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import signal
import sys
import time
from urllib.parse import quote

from benchmark import PROJECT,WORKSPACE,restore_service
sys.path.insert(0,str(PROJECT/'amd_hybrid_deploy_20261031/piper_towel_replica/deployment_entry'))
from fpga_action_authority import FpgaActionAuthority,load_config
from host.task_status import Client
from screen.cooperative_state import CooperativeState
from operation_lock_path import operation_lock


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    from jupyter_client import JupyterBoard
    from control_jupyter import request,PROFILE
    args.out.mkdir(parents=True,exist_ok=False);board=JupyterBoard();active=json.loads(PROFILE.read_text());previous=None
    report=dict(schema='fpga_reset_display_acceptance_v1',passed=False,source='synthetic_test',motion_commands_sent=False,
                eligible_for_policy_execution=False,physical_display_verified=False,cases=[])
    prefix=str(Path(active['board_root']).relative_to('/home/xilinx/jupyter_notebooks'));endpoint='/files/'+quote(prefix+'/runs/',safe='/')
    def call(op,**kwargs):return request(op,board_client=board,**kwargs)
    def capture(name,session,phase,expected_title):
        deadline=time.monotonic()+10
        while True:
            state=board.request('GET',endpoint+'lcd_latest.json').json();m=state.get('maintenance') or {}
            if (state.get('source')=='synthetic_test' and m.get('session')==session and m.get('phase')==phase
                    and state.get('headline')==expected_title):
                png=board.request('GET',endpoint+'lcd_latest.png').content
                if hashlib.sha256(png).hexdigest()==state['image_sha256']:break
            if time.monotonic()>deadline:raise TimeoutError('Reset display did not match '+name)
            time.sleep(.1)
        assert state['task_result']=='unconfirmed' and state['visual_success_confirmed'] is False
        (args.out/(name+'.lcd.png')).write_bytes(png)
        (args.out/(name+'.json')).write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
        report['cases'].append(dict(name=name,session=session,phase=phase,headline=state['headline'],image_sha256=state['image_sha256'],passed=True))
        print('RESET_DISPLAY_CASE='+name,flush=True)
    with operation_lock(PROJECT):
        try:
            previous=call('status')
            path=PROJECT/'flexifold_booth_first_grasp/prepare_hardware.py'
            spec=importlib.util.spec_from_file_location('reset_display_prepare',path);prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)
            prepare.deploy(board,active,call);call('ensure',source='synthetic_test',isolated_lcd=True,bulk_vectors=True)
            # UDP is deliberately ready before slow framebuffer initialization.
            # Await the renderer before testing a short-lived display-only claim.
            ready_deadline=time.monotonic()+45
            while True:
                health=board.request('GET',endpoint+'service_health.json').json()
                if health.get('display_error'):raise RuntimeError(health['display_error'])
                if health.get('source')=='synthetic_test' and health.get('display_ready') is True:break
                if time.monotonic()>ready_deadline:raise TimeoutError('Renderer startup did not complete')
                time.sleep(.2)
            config,profile=load_config(Path(__file__).with_name('required.example.json'))
            authority=FpgaActionAuthority(('192.168.2.99',9009),config,profile)
            authority.status_mode='diagnostic';authority.status_sink='memory';memory=[]
            try:
                began=time.perf_counter();authority.start(countdown=5)
                pose=tuple(v/1e6 for v in profile['start']);zero=(0.,)*14
                authority.send(step=0,elapsed_s=.01,target=pose,measured=pose,previous=pose,act=pose,
                    correction=zero,previous_correction=zero,blended=False,feedback_age_us=1000,queue_depth=0,
                    dispatch=memory.append,deadline_ns=time.perf_counter_ns()+20000000)
                assert len(memory)==1
                report['startup_lease']=dict(passed=True,countdown_s=5,elapsed_s=time.perf_counter()-began,
                    receipt=dict(authority.gate.records[-1]),receiver='memory')
            finally:authority.close(outcome='ended')
            for phase,title in (('RESET_DONE','复位流程已结束'),('RESET_FAILED','复位流程已中断'),('RESET_STOPPED','复位已停止')):
                client=Client(('192.168.2.99',9009))
                try:
                    client.send('RESETTING','EXECUTING','HOST')
                    capture(phase.lower()+'_start',client.session,'RESETTING','双臂正在复位')
                    if phase=='RESET_DONE':
                        time.sleep(2.6);capture('reset_stale',client.session,'RESETTING','复位状态已停更')
                    client.send(phase,'ERROR' if phase=='RESET_FAILED' else 'IDLE','HOST')
                    capture(phase.lower(),client.session,phase,title)
                finally:client.close()
            report['passed']=True
        except (Exception,KeyboardInterrupt) as error:report.update(passed=False,error=type(error).__name__+': '+str(error))
        finally:
            if previous:
                try:report.update(restore_service(call,previous,active['address']))
                except Exception as error:report.update(passed=False,restore_error=str(error))
    (args.out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('RESET_DISPLAY_ACCEPTANCE='+json.dumps(dict(passed=report['passed'],error=report.get('error'),cases=len(report['cases']),motion_commands_sent=False)),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main())
