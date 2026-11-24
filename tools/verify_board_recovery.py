#!/usr/bin/env python3
"""Actual B35 reuse, static-file rollback and interrupted diagnostic recovery.

Uses a harmless comment in a backed-up display-only module. A simulated lost
upload acknowledgement occurs after the real write; recovery must restore exact
prior bytes and the prior service mode. No camera, robot or CAN APIs are used.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[1];PROJECT=ROOT
sys.path[:0]=[str(ROOT/'tools'),str(PROJECT/'npu_deploy/fpga_board'),str(PROJECT/'npu_deploy'),str(PROJECT/'npu_deploy/fpga_authority')]
from jupyter_client import JupyterBoard
from control_jupyter import request,PROFILE
from operation_lock_path import operation_lock
from snapshot_board import snapshot
from restore_board import apply_restore
from benchmark import connect,restore_service
from host.reference import load_profile


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    from board_python import ensure_board_python
    ensure_board_python(__file__)
    args.out.mkdir(parents=True,exist_ok=True);board=JupyterBoard();profile=json.loads(PROFILE.read_text());address=('192.168.2.99',9009)
    prefix=str(Path(profile['board_root']).relative_to('/home/xilinx/jupyter_notebooks'));name='host/action_status.py';endpoint='/files/'+quote(prefix+'/'+name,safe='/')
    report=dict(schema='fpga_actual_recovery_v1',passed=False,robot_commands_sent=False);previous=None;original=None;process=None
    def call(operation,**kwargs):return request(operation,board_client=board,**kwargs)
    def ready():
        client=connect(address,load_profile());client.close()
    def restore(selected,client=board):
        return apply_restore(client,profile,selected,call,snapshot,recovery_directory=args.out/('before_restore_'+str(time.time_ns())),verify_ready=ready)
    try:
        with operation_lock(PROJECT):
            previous=call('status');call('ensure',source='host_observation',isolated_lcd=True,bulk_vectors=True);ready()
            first=call('status');call('ensure',source='host_observation',isolated_lcd=True,bulk_vectors=True);second=call('status')
            assert (first['pid'],first['start_ticks'])==(second['pid'],second['start_ticks'])
            report['same_service_reused']=True
            # Re-entering a task must cancel the legacy delayed finish timer.
            call('finish');call('ensure',source='host_observation',isolated_lcd=True,bulk_vectors=True)
            time.sleep(10.5);ready();after=call('status');assert after['pid']==first['pid'] and after['state']=='running'
            report['scheduled_stop_cancelled']=True
            desired=args.out/'verified_current';snapshot(board,profile,desired)
            original=board.request('GET',endpoint).content
            marker=original+b'\n# Non-actuating rollback rehearsal; no executable changes.\n'
            board.upload(prefix+'/'+name,marker)
            assert board.request('GET',endpoint).content==marker
            report['actual_restore']=restore(desired)
            assert board.request('GET',endpoint).content==original
            board.upload(prefix+'/'+name,marker)
            class LostAcknowledgement:
                def __init__(self):self.failed=False
                def __getattr__(self,key):return getattr(board,key)
                def upload(self,path,data):
                    result=board.upload(path,data)
                    if not self.failed:
                        self.failed=True;raise ConnectionError('Injected lost acknowledgement after the actual board write')
                    return result
            proxy=LostAcknowledgement()
            try:restore(desired,proxy)
            except ConnectionError:pass
            else:raise AssertionError('Injected upload failure was not observed')
            assert proxy.failed and board.request('GET',endpoint).content==marker
            state=call('status');assert state['state']=='running' and state['source']=='host_observation' and state['isolated_lcd'] and state['bulk_vectors']
            report['actual_failed_restore_roll_forward']=dict(passed=True,exact_prior_bytes=True,service_mode_restored=True)
            restore(desired);assert board.request('GET',endpoint).content==original
        # The benchmark itself must own the shared lock during interruption.
        path=args.out/'interrupted_benchmark.json';log=args.out/'interrupted_benchmark.log'
        with log.open('w') as output:
            process=subprocess.Popen([str(PROJECT/'amd_hybrid_deploy_20261031/.venv/bin/python'),str(PROJECT/'npu_deploy/fpga_authority/benchmark.py'),
                '--prepare-board','--samples','750','--out',str(path)],cwd=PROJECT,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
            deadline=time.monotonic()+90
            while 'AUTHORITY_LATENCY_PROGRESS=' not in log.read_text():
                if process.poll() is not None:raise RuntimeError('Benchmark exited before injection; inspect '+str(log))
                if time.monotonic()>deadline:raise TimeoutError('Benchmark did not reach measured traffic')
                time.sleep(.2)
            os.killpg(process.pid,signal.SIGINT);process.wait(timeout=45)
        interrupted=json.loads(path.read_text())
        assert process.returncode==1 and interrupted.get('interrupted') is True and interrupted.get('restored_service_verified') is True
        with operation_lock(PROJECT):
            state=call('status');assert state['state']=='running' and state['source']=='host_observation' and state['isolated_lcd'] and state['bulk_vectors'];ready()
        report.update(interrupted_benchmark=dict(passed=True,report=str(path),service_mode_restored=True),passed=True)
    except (Exception,KeyboardInterrupt) as error:report.update(passed=False,error=type(error).__name__+': '+str(error))
    finally:
        if process and process.poll() is None:
            os.killpg(process.pid,signal.SIGINT);process.wait(timeout=45)
        if previous:
            with operation_lock(PROJECT):
                if original is not None and board.request('GET',endpoint).content!=original:
                    call('stop');board.upload(prefix+'/'+name,original)
                report.update(restore_service(call,previous,profile['address']))
    (args.out/'recovery.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('ACTUAL_BOARD_RECOVERY='+json.dumps(report,ensure_ascii=False),flush=True)
    return 0 if report['passed'] else 1

def interrupted(*_):raise KeyboardInterrupt

if __name__=='__main__':
    signal.signal(signal.SIGTERM,interrupted)
    raise SystemExit(main())
