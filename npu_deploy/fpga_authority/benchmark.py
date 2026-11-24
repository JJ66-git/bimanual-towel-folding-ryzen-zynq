#!/usr/bin/env python3
"""Real-board, non-actuating latency and veto benchmark; never imports Piper/CAN.

Boundary-only results cannot enable policy execution. A successful result still
requires a separate full-controller-load timing acceptance before qualification.
"""
import argparse
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys
import time
import hashlib
import signal
from urllib.parse import quote

PROJECT=Path(__file__).resolve().parents[2]
WORKSPACE=PROJECT/'fpga_integration/fpga_code/fpga_workspace'
sys.path[:0]=[str(WORKSPACE),str(PROJECT/'npu_deploy'),str(PROJECT/'npu_deploy/fpga_board')]
from host.fold_client import FoldClient
from host.action_authority import CheckedSender,AuthorityDenied
from host.protocol import Sample,RUNNING,UNKNOWN
from host.reference import load_profile,profile_crc,evaluate
from host import fold_protocol as wire
from operation_lock_path import operation_lock


def percentile(values,p):
    values=sorted(values);position=(len(values)-1)*p/100;lower=int(position);upper=min(lower+1,len(values)-1)
    return values[lower]+(values[upper]-values[lower])*(position-lower)


def sample(profile,step):
    pose=tuple(profile['start']);zero=(0,)*14
    return Sample(profile_crc(profile),step,0 if step==UNKNOWN else RUNNING,0 if step==UNKNOWN else (step+1)*33333,
                  UNKNOWN if step==UNKNOWN else 1000,0,pose,pose,pose,pose,zero,zero)


def connect(address,profile):
    deadline=time.monotonic()+8
    while True:
        client=FoldClient(address,profile_crc(profile),timeout=.2)
        try:client.probe(sample(profile,UNKNOWN));return client
        except (TimeoutError,ConnectionError):
            client.close()
            if time.monotonic()>=deadline:raise
            time.sleep(.1)


def latency_series(client,profile,count,budget_us,*,clock_ns=time.perf_counter_ns,sleeper=time.sleep,publisher=None):
    rows=[]
    for step in range(count):
        candidate=sample(profile,step);start=clock_ns()
        if publisher:
            from host.action_status import values
            publisher.publish(values('checking',mode='diagnostic',sink='memory',target_sequence=client.sequence,step=step,feedback_age_us=candidate.feedback_age_us,budget_us=budget_us))
        reply=client.send_sample(candidate);end=clock_ns()
        ref=evaluate(candidate,profile)
        matched=reply is not None and reply.values[:4]==(profile_crc(profile),ref['violations'],ref['events'],ref['phase']) and reply.values[8]==wire.BUILD_ID
        if publisher:
            publisher.publish(values('allowed' if matched and end-start<=budget_us*1000 else 'denied',mode='diagnostic',sink='memory',
                target_sequence=reply.sequence if reply else client.sequence-1,step=step,feedback_age_us=candidate.feedback_age_us,
                budget_us=budget_us,mask=reply.values[1] if reply else UNKNOWN,round_trip_us=min(UNKNOWN,int((end-start)/1000)),
                reason='' if matched and end-start<=budget_us*1000 else 'no_matching_reply' if reply is None else 'reply_or_processing_late'))
        rows.append(dict(step=step,started_ns=start,returned_ns=end,round_trip_us=(end-start)/1000,matched=matched,
                         sequence=reply.sequence if reply else None,session=client.session,
                         mask=reply.values[1] if reply else None,within_budget=matched and end-start<=budget_us*1000))
        if step%25==0:print('AUTHORITY_LATENCY_PROGRESS='+str(step+1)+'/'+str(count),flush=True)
        remaining=1/30-(clock_ns()-start)/1e9
        if remaining>0:sleeper(remaining)
    values=[row['round_trip_us']/1000 for row in rows]
    return dict(samples=count,matched=sum(r['matched'] for r in rows),deadline_misses=sum(not r['within_budget'] for r in rows),
                rtt_ms={str(p):percentile(values,p) for p in (50,95,99,100)},rows=rows)


def veto_check(client,profile,budget_us):
    from dataclasses import replace
    gate=CheckedSender(client,profile,reply_budget_us=budget_us,feedback_limit_us=100000)
    sink=[];pose=tuple(v/1e6 for v in profile['start'])
    gate.send(sample(profile,0),pose,sink.append,deadline_ns=time.perf_counter_ns()+budget_us*1000)
    bad=list(profile['start']);bad[8]=2650000
    rejected=replace(sample(profile,1),target=tuple(bad))
    try:gate.send(rejected,tuple(v/1e6 for v in bad),sink.append,deadline_ns=time.perf_counter_ns()+budget_us*1000)
    except AuthorityDenied as error:
        if error.reason!='pl_violation':raise
    else:raise RuntimeError('PL did not veto the injected diagnostic candidate')
    try:gate.send(sample(profile,2),pose,sink.append,deadline_ns=time.perf_counter_ns()+budget_us*1000)
    except AuthorityDenied as error:
        if not error.reason.startswith('latched:'):raise
    else:raise RuntimeError('A latched denial was bypassed')
    if len(sink)!=1:raise RuntimeError('Rejected candidate reached the diagnostic sink')
    return dict(passed=True,sink='in_memory_only',accepted_callbacks=len(sink),rejected_callbacks=0,records=list(gate.records))


def restore_service(call,previous,address):
    if previous['state']!='running':
        state=call('stop')
        if state.get('state')!='stopped':raise RuntimeError('Diagnostic service was not stopped')
        return dict(restored_service=state,restored_service_verified=True,restored_state='stopped')
    state=call('ensure',source=previous.get('source') or 'host_observation',lcd=previous.get('lcd',True),
        isolated_lcd=previous.get('isolated_lcd',False),bulk_vectors=previous.get('bulk_vectors',False))
    from fpga_service_probe import probe
    probe(address)
    return dict(restored_service=state,restored_service_verified=True,restored_state='running')


def display_check(board,active,server_pid,expected,output,timeout=20):
    prefix=str(Path(active['board_root']).relative_to('/home/xilinx/jupyter_notebooks'))
    endpoint='/files/'+quote(prefix+'/runs/',safe='/');deadline=time.monotonic()+timeout
    last={}
    while True:
        try:
            health=board.request('GET',endpoint+'service_health.json').json();last['health']=health
            if health.get('pid')==server_pid and health.get('display_error'):
                raise RuntimeError('LCD worker failed: '+health['display_error'])
            state=board.request('GET',endpoint+'lcd_latest.json').json();last['state']=state
            matched=(health.get('pid')==server_pid and health.get('display_ready') is True
                and health.get('renderer_pid')==state.get('renderer_pid') and state.get('renderer_pid')!=server_pid
                and state.get('render_worker')=='process' and state.get('source')=='synthetic_test'
                and all(state.get(key)==value for key,value in expected.items()))
            if matched:
                png=board.request('GET',endpoint+'lcd_latest.png').content
                if hashlib.sha256(png).hexdigest()==state.get('image_sha256'):
                    output=Path(output);output.write_bytes(png)
                    return dict(passed=True,health=health,snapshot=state,image=str(output),physical_display_verified=False)
        except RuntimeError:raise
        except Exception as error:last['read_error']=str(error)
        if time.monotonic()>=deadline:raise TimeoutError('LCD did not match the current PL reply: '+json.dumps(last,ensure_ascii=False))
        time.sleep(.2)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples',type=int,default=750)
    parser.add_argument('--reply-budget-us',type=int,default=20000)
    parser.add_argument('--prepare-board',action='store_true',help='snapshot, synchronize paired runtime and select isolated LCD/bulk MMIO')
    parser.add_argument('--fault-matrix',action='store_true',help='run all non-actuating byte-level fault cases on the real PL')
    parser.add_argument('--plan',action='store_true')
    parser.add_argument('--out',type=Path)
    args=parser.parse_args();profile=load_profile()
    if not 1<=args.samples<=profile['max_actions'] or not 0<args.reply_budget_us<33333:parser.error('Invalid sample count or dispatch budget')
    if args.plan:
        print('Shared rig lock -> read-only board snapshot before updates -> synthetic B35 + isolated LCD + bulk MMIO -> latency -> fresh session -> real PL veto with memory sink -> restore prior service mode. No CAN/robot imports.');return 0
    from jupyter_client import JupyterBoard
    from control_jupyter import request,PROFILE
    active=json.loads(PROFILE.read_text());board=JupyterBoard()
    output=args.out or PROJECT/'npu_deploy/artifacts/fpga_authority'/('benchmark_'+str(time.time_ns())+'.json')
    output.parent.mkdir(parents=True,exist_ok=True)
    report=dict(schema='fpga_authority_boundary_benchmark_v1',checked_at=datetime.now().astimezone().isoformat(),
                passed=False,eligible_for_policy_execution=False,full_controller_load_measured=False,
                input_source='synthetic_test',actuator='in_memory_only',robot_commands_sent=False,build_id=wire.BUILD_ID)
    sys.path.insert(0,str(PROJECT/'amd_hybrid_deploy_20261031/piper_towel_replica/deployment_entry'))
    from fpga_action_authority import implementation_identity
    report.update(implementation_sha256=implementation_identity(),boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    previous=None;changed=False;clients=[]
    with operation_lock(PROJECT):
        try:
            board.request('GET','/login',timeout=3)
            if not board.authenticated():raise RuntimeError('No authenticated board session; no changes were made')
            def call(op,**kwargs):
                nonlocal changed
                if op!='status':changed=True
                return request(op,board_client=board,**kwargs)
            previous=call('status')
            if previous['state']!='running' and not args.prepare_board:
                raise RuntimeError('Managed B35 service is stopped; use --prepare-board to start and restore it automatically')
            if args.prepare_board:
                path=PROJECT/'flexifold_booth_first_grasp/prepare_hardware.py'
                spec=importlib.util.spec_from_file_location('authority_board_prepare',path)
                prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)
                prepare.deploy(board,active,call)
                call('ensure',source='synthetic_test',isolated_lcd=True,bulk_vectors=True)
            state=call('status');report['board_service']=state
            if state.get('source')!='synthetic_test' or state.get('isolated_lcd') is not True or state.get('bulk_vectors') is not True:
                raise RuntimeError('Benchmark requires the explicit synthetic isolated-LCD/bulk-MMIO service')
            address=('192.168.2.99',9009)
            client=connect(address,profile);clients.append(client)
            from host.action_status_publisher import Publisher
            publisher=Publisher(address,client.session)
            try:
                report['latency']=latency_series(client,profile,args.samples,args.reply_budget_us,publisher=publisher)
            finally:publisher.close()
            report['display_telemetry']=dict(transmitted=publisher.sequence,acknowledged=publisher.acknowledged,last_error=publisher.error,policy_thread_socket_wait=False)
            report['passed']=publisher.acknowledged>0
            client.close()
            client=connect(address,profile);clients.append(client)
            report['veto']=veto_check(client,profile,args.reply_budget_us)
            rejected=report['veto']['records'][1]
            report['display']=display_check(board,active,state['pid'],dict(session=client.session,
                sequence=rejected['sequence'],mask=rejected['violations']),output.with_suffix('.lcd.png'))
            report['passed']=report['passed'] and report['latency']['deadline_misses']==0 and report['veto']['passed'] and report['display']['passed']
            if args.fault_matrix:
                from faults import run_matrix
                report['fault_matrix']=run_matrix(real_board=True,budget_us=args.reply_budget_us)
                report['passed']=report['passed'] and report['fault_matrix']['passed']
        except KeyboardInterrupt:
            report.update(passed=False,interrupted=True,error='Operator requested interruption; restoring prior service state')
        except Exception as error:
            report.update(passed=False,error=type(error).__name__+': '+str(error))
            if changed:
                try:report['board_diagnostics']=prepare.collect_diagnostics(board,active)
                except Exception as diagnostic_error:report['diagnostic_error']=str(diagnostic_error)
        finally:
            for client in clients:client.close()
            if changed and previous:
                try:
                    report.update(restore_service(lambda operation,**kwargs:request(operation,board_client=board,**kwargs),previous,active['address']))
                except Exception as error:report.update(passed=False,restore_error=str(error))
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('AUTHORITY_BOUNDARY_BENCHMARK='+json.dumps(dict(passed=report['passed'],error=report.get('error'),report=str(output),eligible_for_policy_execution=False,robot_commands_sent=False)),flush=True)
    return 0 if report['passed'] else 1


def abort_on_signal(*_):raise KeyboardInterrupt


if __name__=='__main__':
    signal.signal(signal.SIGTERM,abort_on_signal)
    raise SystemExit(main())
