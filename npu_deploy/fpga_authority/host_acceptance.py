#!/usr/bin/env python3
"""Joint model/encoder/FPGA diagnostic load with no cameras or actuator commands.

This is representative fixed-input load, not full real-controller qualification.
The benchmark owns the rig lock and restores the previous board service mode.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import resource

PROJECT=Path(__file__).resolve().parents[2];HERE=Path(__file__).resolve().parent
PYTHON=PROJECT/'amd_hybrid_deploy_20261031/.venv/bin/python'


def capabilities():
    result={name:Path(path).exists() for name,path in (('kfd','/dev/kfd'),('dri','/dev/dri'),('xdna','/dev/accel'))}
    try:
        with socket.create_connection(('192.168.2.99',9090),timeout=2):pass
        result['board_tcp']=True
    except OSError as error:result.update(board_tcp=False,board_error=type(error).__name__+': '+str(error))
    return result


def load_overlap(model,board):
    intervals=[(timing['started_ns'],timing['returned_ns']) for row in model.get('rows',[]) for timing in (row['act'],row['smol_guided'])]
    samples=board.get('latency',{}).get('rows',[])
    count=sum(any(lo<row['returned_ns'] and hi>row['started_ns'] for lo,hi in intervals) for row in samples)
    return dict(samples=len(samples),samples_overlapping_model_inference=count,clock='shared host perf_counter_ns',
        actual_camera_can_load=False)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--execute',action='store_true');p.add_argument('--backend',choices=['cpu','rocm'],default='rocm')
    p.add_argument('--vision-worker',choices=['native','cpu','npu'],default='npu');p.add_argument('--samples',type=int,default=750)
    p.add_argument('--fixture',type=Path)
    p.add_argument('--transport',choices=['board','reference'],default='board',help='reference measures in-memory protocol cost; it is not a PL/network benchmark')
    args=p.parse_args()
    if not 1<=args.samples<=750:p.error('samples must be within the selected 750-action budget')
    args.out.mkdir(parents=True,exist_ok=True)
    report=dict(schema='fpga_fixed_input_joint_acceptance_v1',checked_at=datetime.now().astimezone().isoformat(),passed=False,
        eligible_for_policy_execution=False,full_controller_load_measured=False,robot_commands_sent=False)
    report.update(transport=args.transport,real_pl=False)
    report['capabilities']=caps=capabilities();reasons=[]
    if args.transport=='board' and not caps['board_tcp']:reasons.append(caps['board_error'])
    if args.backend=='rocm' and not (caps['kfd'] and caps['dri']):reasons.append('ROCm device nodes are absent')
    if args.vision_worker=='npu' and not caps['xdna']:reasons.append('XDNA device nodes are absent')
    if args.vision_worker=='npu' and caps['xdna']:
        soft,hard=resource.getrlimit(resource.RLIMIT_MEMLOCK);required=1<<30
        report['memlock']=dict(soft=soft,hard=hard,required=required)
        if hard!=resource.RLIM_INFINITY and hard<required:reasons.append('NPU requires a 1 GiB memlock limit; this process cannot raise the inherited hard limit')
        elif soft!=resource.RLIM_INFINITY and soft<required:resource.setrlimit(resource.RLIMIT_MEMLOCK,(required,hard))
    report['blocking_reasons']=reasons
    if not args.execute or reasons:
        report['status']='blocked' if reasons else 'planned'
        (args.out/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2));return 2 if reasons and args.execute else 0
    session=args.out/('session_'+str(time.time_ns()));session.mkdir();ready=session/'ready.json';stop=session/'stop'
    model_path=session/'model_load.json';board_path=session/'board_load.json';process=None;board_process=None
    try:
        with (session/'model_load.log').open('w') as log:
            command=[str(PYTHON),str(HERE/'model_workload.py'),'--backend',args.backend,'--vision-worker',args.vision_worker,
                '--duration-s','3600','--ready-file',str(ready),'--stop-file',str(stop),'--out',str(model_path)]
            if args.fixture:command+=['--fixture',str(args.fixture.resolve())]
            process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            deadline=time.monotonic()+900;last_notice=0.
            while not ready.exists():
                if process.poll() is not None:raise RuntimeError('Model load exited before readiness; see '+str(session/'model_load.log'))
                if time.monotonic()>=deadline:raise TimeoutError('Model/encoder startup exceeded 900 seconds')
                if time.monotonic()-last_notice>10:print('JOINT_ACCEPTANCE: loading real checkpoints and encoder',flush=True);last_notice=time.monotonic()
                time.sleep(.5)
            observed=json.loads(ready.read_text())
            if observed.get('pid')!=process.pid or observed.get('passed') is not True:raise RuntimeError('Readiness does not belong to this verified worker')
            if args.transport=='board':
                with (session/'board_load.log').open('w') as board_log:
                    board_process=subprocess.Popen([str(PYTHON),str(HERE/'benchmark.py'),'--prepare-board','--fault-matrix','--samples',str(args.samples),
                        '--out',str(board_path)],stdout=board_log,stderr=subprocess.STDOUT,start_new_session=True)
                    code=board_process.wait()
            else:
                from benchmark import sample,latency_series
                from faults import reference_socket_factory,run_matrix
                from host.fold_client import FoldClient
                from host.reference import load_profile,profile_crc
                from host.protocol import UNKNOWN
                profile=load_profile()
                client=FoldClient(('127.0.0.1',9009),profile_crc(profile),socket_factory=reference_socket_factory(profile))
                try:
                    client.probe(sample(profile,UNKNOWN));latency=latency_series(client,profile,args.samples,20000)
                finally:client.close()
                faults=run_matrix()
                protocol=dict(schema='reference_protocol_under_cpu_model_load_v1',passed=latency['deadline_misses']==0 and faults['passed'],
                    latency=latency,fault_matrix=faults,real_pl=False,network_socket_used=False,physical_lcd_used=False,
                    measurement='in-memory FoldClient/FoldSession processing under separate real-model CPU load; not Ethernet/PL round trip')
                board_path.write_text(json.dumps(protocol,indent=2)+'\n');code=0 if protocol['passed'] else 1
            if process.poll() is not None:raise RuntimeError('Model load stopped before board acceptance completed')
            stop.touch();process.wait(timeout=120)
            model=json.loads(model_path.read_text());board=json.loads(board_path.read_text())
            report['real_pl']=args.transport=='board' and board.get('latency',{}).get('matched',0)>0
            overlap=load_overlap(model,board)
            report.update(status='completed',model_report=str(model_path),board_report=str(board_path),overlap=overlap,
                passed=code==0 and process.returncode==0 and model['passed'] and board['passed'] and overlap['samples_overlapping_model_inference']>0,
                gpu_execution=model['gpu_execution'],npu_execution=model['npu_execution'])
    except (Exception,KeyboardInterrupt) as error:report.update(status='interrupted' if isinstance(error,KeyboardInterrupt) else 'failed',error=type(error).__name__+': '+str(error),session=str(session))
    finally:
        if board_process and board_process.poll() is None:
            try:os.killpg(board_process.pid,signal.SIGINT)
            except ProcessLookupError:pass
            try:board_process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                os.killpg(board_process.pid,signal.SIGKILL);board_process.wait();report['board_restore_unverified']='Diagnostic child exceeded its 45 second cleanup window; inspect its saved service state'
        stop.touch()
        if process and process.poll() is None:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
        (args.out/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
    print('JOINT_ACCEPTANCE='+json.dumps(report),flush=True);return 0 if report['passed'] else 1


def abort_on_signal(*_):raise KeyboardInterrupt


if __name__=='__main__':
    signal.signal(signal.SIGTERM,abort_on_signal)
    raise SystemExit(main())
