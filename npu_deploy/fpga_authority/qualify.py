#!/usr/bin/env python3
"""Derive execution qualification from full live evidence; missing evidence fails closed.

Inputs: a full physical trial, three controlled physical exit trials, actual
board fault acceptance, original SDK/camera recordings, onsite observations.
Memory runs and fixed-image load reports cannot produce a qualified config.
No camera, CAN, board write, actuator, or model execution occurs here.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

PROJECT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(PROJECT/'amd_hybrid_deploy_20261031/piper_towel_replica/deployment_entry'),str(Path(__file__).parent)]
from fpga_action_authority import implementation_identity,load_config
from host.fold_protocol import BUILD_ID
from faults import CASES


def digest(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def read(path):return json.loads(Path(path).read_text())


def validate_trial(data,*,mode,identity,boot_id,config):
    """Pure checks; all input file identities are retained by collect_trial()."""
    errors=[]
    def need(condition,message):
        if not condition:errors.append(message)
    process=data['process'];trace=data['trace'];manifest=data['manifest'];plan=trace.get('commissioning') or {}
    records=data['decisions'];actions=data['actions'];rows=trace.get('rows',[]);n=750 if mode=='full' else 5
    need(process.get('motion_requested') is True and process.get('onsite_operator_confirmed') is True,'physical_trial_not_confirmed')
    need(process.get('returncode')==(0 if mode=='full' else 130 if mode=='operator_stop' else 1),'unexpected_process_exit')
    need(trace.get('execute') is True and manifest.get('execute') is True,'memory_or_readonly_trial')
    need(manifest.get('implementation_sha256')==identity and manifest.get('boot_id')==boot_id,'stale_trial_implementation_or_boot')
    service=manifest.get('config',{}).get('_verified_service',{})
    need(service.get('state')=='running' and service.get('source')=='host_observation'
         and service.get('isolated_lcd') is True and service.get('bulk_vectors') is True
         and service.get('implementation_sha256')==plan.get('board_implementation_sha256'),'unverified_or_wrong_live_board_service')
    need(plan.get('stage')==('full' if mode=='full' else 'small') and plan.get('stop_mode')==('limit' if mode=='full' else mode),'wrong_trial_scope')
    need(plan.get('asset_sha256')==data.get('current_asset_sha256') and bool(plan.get('asset_sha256')),'trial_assets_changed')
    need(plan.get('address')==config['address'] and plan.get('guard_profile_sha256')==config['guard_profile_sha256']
         and plan.get('reply_budget_us')==config['reply_budget_us'] and plan.get('feedback_limit_us')==config['feedback_limit_us'],'trial_contract_changed')
    need([r.get('step') for r in rows]==list(range(n)),'incomplete_live_control_sequence')
    allowed=[r for r in records if r.get('event')=='authority_decision' and r.get('decision')=='ALLOW']
    need([r.get('step') for r in allowed]==list(range(n)) and [r.get('sequence') for r in allowed]==list(range(1,n+1)),'missing_per_target_authority')
    need(len({r.get('session') for r in records})==1,'trial_sessions_mixed')
    need(len(actions)==n+1,'sdk_action_recording_incomplete_or_unexpected_extra_sends')
    names=data['action_names'];deadline_misses=0
    for i,r in enumerate(allowed):
        need(r.get('dispatched') is True and r.get('violations')==0 and r.get('reference_match') is True and r.get('build_id')==BUILD_ID,'invalid_allow_receipt')
        started=r.get('dispatch_started_ns',0);deadline=r.get('deadline_ns',0)
        if not (0<r.get('reply_received_ns',0)<=started<deadline):deadline_misses+=1
        need(0<=r.get('feedback_age_us',0xffffffff)<=config['feedback_limit_us'],'stale_feedback_in_trial')
        if i<len(actions) and i<len(rows):
            a=actions[i];sdk_start=a.get('send_started_monotonic_s',0)*1e9
            if not (started-2000<=sdk_start<deadline and sdk_start<=r.get('dispatch_returned_ns',0)+2000):deadline_misses+=1
            target=rows[i].get('target',[]);requested=a.get('requested',{})
            need(len(target)==14 and set(requested)==set(names) and all(abs(requested.get(name,float('inf'))-v)<1e-8 for name,v in zip(names,target)),'sdk_target_differs_from_checked_candidate')
    need(deadline_misses==0,'dispatch_deadline_missed_or_not_recorded')
    hold=trace.get('exit_hold') or {}
    need(hold.get('command_returned') is True and hold.get('physical_hold_verified') is False,'native_exit_hold_missing_or_overclaimed')
    need(data.get('camera_recording_complete') is True,'real_camera_recording_missing')
    observation=data.get('onsite_observation',{});session=allowed[0].get('session') if allowed else None
    need(observation.get('schema')=='fpga_trial_onsite_observation_v1' and observation.get('session')==session
         and observation.get('observer_kind')=='human_onsite','onsite_observation_missing_or_wrong_session')
    for flag in ('motion_smooth','no_interference','gripper_observed','stop_hold_stable'):
        need(observation.get(flag) is True,'onsite_'+flag+'_unconfirmed')
    if mode=='full':
        need(trace.get('execution_failure') is None and trace.get('termination_reason')=='action_budget','full_trial_did_not_complete')
        need(any(r.get('step',0)>=300 and r.get('smol_candidate') is not None for r in rows),'smolvla_live_candidate_missing')
        need('HANDOFF_REQUEST step=300' in data['console'] and 'NPU_FIRST_INFERENCE_COMPLETED' in data['console']
             and 'NPU_EXECUTION_CONFIRMED' in data['console'],'live_handoff_or_actual_npu_evidence_missing')
    elif mode in ('pl_veto','reply_loss'):
        expected='pl_violation' if mode=='pl_veto' else 'no_matching_reply'
        faults=[r for r in records if r.get('event')=='commissioning_fault']
        need(len(faults)==1 and faults[0].get('reason')==expected and faults[0].get('dispatched') is False,'controlled_fault_not_rejected')
        need(bool(trace.get('execution_failure')),'controlled_fault_did_not_take_error_exit')
    else:
        need(trace.get('termination_reason')=='manual_stop' and trace.get('execution_failure') is None,'controlled_stop_did_not_take_stop_exit')
    return dict(passed=not errors,errors=sorted(set(errors)),mode=mode,allowed=len(allowed),deadline_misses=deadline_misses,
                session=session,board_implementation_sha256=plan.get('board_implementation_sha256'))


def collect_trial(directory):
    directory=Path(directory);process=read(directory/'process.json')
    if process.get('schema')!='fpga_policy_commissioning_process_v1':
        raise ValueError('A full live commissioned trial is required; read-only/boundary runs cannot qualify')
    trace=read(process['trace'])
    authority=Path(trace['authority_run']);manifest=read(authority/'manifest.json')
    ledger=[json.loads(l) for l in (authority/'authority.jsonl').read_text().splitlines()]
    recording=Path(process['recording']);events=[json.loads(l) for l in (recording/'robot_events.jsonl').read_text().splitlines()]
    summary=read(recording/'recording_summary.json');plan=trace.get('commissioning') or {}
    from host.reference import load_profile
    files=[directory/'process.json',directory/'console.log',Path(process['trace']),authority/'manifest.json',authority/'authority.jsonl',
           recording/'robot_events.jsonl',recording/'frame_times.jsonl',recording/'recording_summary.json']
    videos=[recording/(side+'.avi') for side in ('left','middle','right')]
    camera_complete=all(p.is_file() and p.stat().st_size>0 for p in videos) and summary.get('writer_finished') is True and summary.get('events_dropped')==0
    files += [p for p in videos if p.is_file()]
    observation_path=directory/'onsite_observation.json';observation={}
    if observation_path.exists():observation=read(observation_path);files.append(observation_path)
    return dict(process=process,trace=trace,manifest=manifest,decisions=ledger,actions=[e for e in events if e.get('kind')=='action'],
        action_names=load_profile()['action_names'],camera_recording_complete=camera_complete,onsite_observation=observation,
        console=(directory/'console.log').read_text(),current_asset_sha256={p:digest(p) for p in plan.get('asset_sha256',{})},
        evidence_sha256={str(p.resolve()):digest(p) for p in files})


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--full-trial',type=Path,required=True)
    parser.add_argument('--exit-trial',action='append',type=Path,default=[]);parser.add_argument('--fault-benchmark',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False);identity=implementation_identity();boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    config,_=load_config(Path(__file__).with_name('required.example.json'));errors=[];trials=[];evidence={};full=None
    report=dict(schema='fpga_authority_qualification_v1',passed=False,eligible_for_policy_execution=False,real_pl=False,
        full_controller_load_measured=False,build_id=BUILD_ID,implementation_sha256=identity,boot_id=boot,
        address=config['address'],guard_profile_sha256=config['guard_profile_sha256'],reply_budget_us=config['reply_budget_us'],
        feedback_limit_us=config['feedback_limit_us'],fps=30,fault_cases_passed=False,deadline_misses=None,
        physical_can_interlock=False,fold_success_certified=False,errors=errors,trials=trials)
    try:
        full=collect_trial(args.full_trial);result=validate_trial(full,mode='full',identity=identity,boot_id=boot,config=config)
        trials.append(result);errors.extend(result['errors']);evidence.update(full['evidence_sha256'])
        modes=set()
        for path in args.exit_trial:
            data=collect_trial(path);mode=(data['trace'].get('commissioning') or {}).get('stop_mode');modes.add(mode)
            if mode not in ('operator_stop','pl_veto','reply_loss'):raise ValueError('Unexpected controlled exit trial')
            result=validate_trial(data,mode=mode,identity=identity,boot_id=boot,config=config)
            trials.append(result);errors.extend(result['errors']);evidence.update(data['evidence_sha256'])
        if modes!={'operator_stop','pl_veto','reply_loss'}:errors.append('three_controlled_exit_trials_required')
        board_hash=trials[0]['board_implementation_sha256']
        if any(t['board_implementation_sha256']!=board_hash for t in trials):errors.append('mixed_board_implementations')
        faults=read(args.fault_benchmark);matrix=faults.get('fault_matrix',{})
        if not (faults.get('passed') is True and faults.get('implementation_sha256')==identity and faults.get('boot_id')==boot
                and faults.get('board_service',{}).get('implementation_sha256')==board_hash
                and matrix.get('passed') is True and matrix.get('real_pl') is True
                and {c.get('case') for c in matrix.get('cases',[]) if c.get('passed') is True}==set(CASES)):
            errors.append('current_real_board_fault_matrix_missing')
        evidence[str(args.fault_benchmark.resolve())]=digest(args.fault_benchmark)
        report.update(deadline_misses=sum(t['deadline_misses'] for t in trials),board_implementation_sha256=board_hash)
        if not errors:
            report.update(passed=True,eligible_for_policy_execution=True,real_pl=True,full_controller_load_measured=True,
                          fault_cases_passed=True,asset_sha256=full['current_asset_sha256'])
    except (OSError,ValueError,KeyError,TypeError) as error:errors.append(type(error).__name__+': '+str(error))
    report['evidence_sha256']=evidence
    (args.out/'qualification.json').write_text(json.dumps(report,indent=2)+'\n')
    if report['passed']:
        config['qualification_report']=str((args.out/'qualification.json').resolve())
        (args.out/'required.qualified.json').write_text(json.dumps(config,indent=2)+'\n')
    print('AUTHORITY_QUALIFICATION='+json.dumps(dict(passed=report['passed'],eligible_for_policy_execution=report['eligible_for_policy_execution'],errors=errors)),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
