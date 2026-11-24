#!/usr/bin/env python3
"""Bounded commissioning, separate from qualified learned-policy execution.

Default: fresh passive SDK feedback, real PL checks, memory receiver only.
The sole physical trajectory holds all joints and opens both grippers by 5 mm
over two seconds, then returns over two seconds. No enable/reset/zero/disable.
A physical run requires a reviewed, fresh plan and its exact SHA-256. It never
creates a policy qualification or enables the general --execute entrypoint.
"""
import argparse
import hashlib
import json
import math
import os
import signal
import sys
import time
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[2]
POLICY=PROJECT/'amd_hybrid_deploy_20261031/piper_towel_replica'
sys.path[:0]=[str(PROJECT/'npu_deploy'),str(POLICY/'deployment_entry'),
              str(POLICY/'runtime_source/src'),str(POLICY/'scripts'),
              str(POLICY/'upstream/act_reference/src'),str(POLICY.parent/'shared_runtime')]
from fpga_action_authority import FpgaActionAuthority,dispatch_policy_target,load_config,implementation_identity
from fpga_feedback_age import FeedbackAgeClock
from host.action_authority import AuthorityDenied

JOINTS=tuple(i for i in range(14) if i not in (6,13))
GRIPPERS=(6,13)
CAPS=dict(joint_target_change_rad=0.0,gripper_open_m=0.005,
          gripper_command_speed_m_s=0.0025,fps=20,commands=100,
          initial_joint_drift_rad=0.01,initial_gripper_drift_m=0.002,
          joint_tracking_rad=0.02,gripper_tracking_m=0.006,max_run_s=15)


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def identity():
    return dict(implementation_identity(),commissioning=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def validate_anchor(pose,profile):
    if len(pose)!=14 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in pose):
        raise ValueError('Fourteen finite feedback positions required')
    for i,v in enumerate(pose):
        maximum=v+(CAPS['gripper_open_m'] if i in GRIPPERS else 0)
        if v<profile['lower'][i]/1e6 or maximum>profile['upper'][i]/1e6:
            raise ValueError('Bounded trajectory outside guard envelope')


def trajectory(anchor):
    for step in range(CAPS['commands']):
        # 40 opening increments, 20 open holds, 40 closing increments.
        fraction=(step+1)/40 if step<40 else 1 if step<60 else (99-step)/40
        target=list(anchor)
        for i in GRIPPERS:target[i]+=CAPS['gripper_open_m']*fraction
        yield tuple(target)


def validate_target(target,anchor,measured,profile):
    validate_anchor(anchor,profile)
    if len(target)!=14 or len(measured)!=14 or not all(math.isfinite(v) for v in (*target,*measured)):
        raise ValueError('Invalid commissioning target or feedback')
    for i in range(14):
        if i in JOINTS:
            if target[i]!=anchor[i] or abs(measured[i]-anchor[i])>CAPS['joint_tracking_rad']:
                raise ValueError('Fixed joint envelope exceeded')
        elif not anchor[i]-1e-12<=target[i]<=anchor[i]+CAPS['gripper_open_m']+1e-12:
            raise ValueError('Gripper commissioning envelope exceeded')
        limit=CAPS['joint_tracking_rad'] if i in JOINTS else CAPS['gripper_tracking_m']
        if abs(target[i]-measured[i])>limit:raise ValueError('Commissioning tracking limit exceeded')


def validate_plan(plan,pose,profile,board,serials,*,now_ns=None):
    now_ns=time.perf_counter_ns() if now_ns is None else now_ns
    if (plan.get('schema')!='bounded_gripper_commissioning_plan_v1' or plan.get('caps')!=CAPS
            or plan.get('implementation_sha256')!=identity()
            or plan.get('boot_id')!=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            or plan.get('board_implementation_sha256')!=board.get('implementation_sha256')
            or plan.get('usb_serials')!=serials):raise ValueError('Stale or changed commissioning plan')
    if not plan['created_ns']<=now_ns<plan['expires_ns']<=plan['created_ns']+900_000_000_000:
        raise ValueError('Commissioning plan expired')
    anchor=tuple(plan['anchor']);validate_anchor(anchor,profile)
    for i in range(14):
        limit=CAPS['initial_joint_drift_rad'] if i in JOINTS else CAPS['initial_gripper_drift_m']
        if abs(pose[i]-anchor[i])>limit:raise ValueError('Pose changed since plan review')
    return anchor


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--execute-plan',type=Path)
    parser.add_argument('--plan-sha256')
    args=parser.parse_args()
    if bool(args.execute_plan)!=bool(args.plan_sha256):parser.error('Execution requires plan and exact reviewed SHA-256')
    execute=args.execute_plan is not None
    plan=None
    if execute:
        raw=args.execute_plan.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=args.plan_sha256:raise ValueError('Reviewed plan digest mismatch')
        plan=json.loads(raw)
    from operation_lock_path import operation_lock
    from prepare_cobot import can_inventory
    from lerobot.robots.piper_dual.piper_dual import PIPERDual
    from lerobot.robots.piper_dual.config_piper_dual import PIPERDualConfig
    from lerobot.rollout.robot_wrapper import ThreadSafeRobot,DispatchNotStarted
    from cobot_connection import gripper_snapshot
    from cobot_reset import driver_snapshot
    sys.path.insert(0,str(PROJECT/'npu_deploy/fpga_board'))
    from control_jupyter import request
    from host.action_status_publisher import Publisher
    site=json.loads((POLICY/'configs/cobot_site.json').read_text())
    config,profile=load_config(PROJECT/'npu_deploy/fpga_authority/required.example.json',execute=False)
    if float(os.environ.get('PIPER_ACTION_FILTER_ALPHA','1'))!=1:
        raise ValueError('Commissioning requires unfiltered native SDK targets')
    os.environ['AIPC_FPGA_FOLD']=config['address']  # Select paired monotonic SDK snapshots.
    address=config['address'].rsplit(':',1);address=(address[0],int(address[1]))
    args.out.mkdir(parents=True,exist_ok=False)
    report=dict(schema='bounded_gripper_commissioning_v1',passed=False,execute=execute,
                full_controller_load_measured=False,eligible_for_policy_execution=False,
                physical_can_interlock=False,actuator_callback_started=0,actuator_callback_returned=0)
    stopped=False
    def stop(*_):
        nonlocal stopped
        stopped=True
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    robot=authority=log=None
    try:
        with operation_lock(PROJECT):
            inventory=can_inventory();ports={side:site['robot'][side+'_port'] for side in ('left','right')}
            serials=site['usb_serials']
            for side,port in ports.items():
                if [r['serial'] for r in inventory if r['interface']==port]!=[serials[side]]:
                    raise ValueError('USB-CAN serial binding differs from reviewed site')
            board=request('status')
            if not (board.get('state')=='running' and board.get('source')=='host_observation'
                    and board.get('isolated_lcd') is True and board.get('bulk_vectors') is True
                    and board.get('lcd') is True and board.get('implementation_sha256')):
                raise ValueError('Expected optimized live board with LCD')
            robot=PIPERDual(PIPERDualConfig(left_port=ports['left'],right_port=ports['right'],read_only=True,cameras={}))
            robot.connect();wrapper=ThreadSafeRobot(robot);clock=FeedbackAgeClock();time.sleep(1)
            observations=[]
            names=tuple(profile['action_names'])
            def feedback():
                observation,timestamps,sampled_ns=wrapper.get_feedback_snapshot(timeout=config['reply_budget_us']/1e6)
                pose=tuple(observation[name] for name in names);age=clock.age_us(timestamps)
                if age>config['feedback_limit_us']:raise ValueError('Feedback unavailable or stale')
                observations.append(dict(pose=pose,age_us=age,sampled_ns=sampled_ns))
                return pose,age,sampled_ns
            def healthy():
                driver=driver_snapshot(robot);grip=gripper_snapshot(robot)
                for side in ('left','right'):
                    if not all(driver[side]['enabled']) or any(int(v,16) for v in driver[side]['fault_bits']):
                        raise ValueError('Joint driver disabled/faulted; no automatic enable')
                    if not grip[side].get('enabled') or grip[side].get('fault_bits')!='0x0':
                        raise ValueError('Gripper disabled/faulted; no automatic recovery')
                return dict(driver=driver,grippers=grip)
            current,age,_=feedback();report['before']=healthy()
            if execute:anchor=validate_plan(plan,current,profile,board,serials)
            else:
                anchor=current;validate_anchor(anchor,profile);now=time.perf_counter_ns()
                plan=dict(schema='bounded_gripper_commissioning_plan_v1',anchor=anchor,caps=CAPS,
                          created_ns=now,expires_ns=now+900_000_000_000,
                          implementation_sha256=identity(),board_implementation_sha256=board['implementation_sha256'],
                          boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),usb_serials=serials,
                          feedback_age_us=age,requires_operator_confirmation=True)
            log=(args.out/'authority.jsonl').open('x',buffering=1)
            authority=FpgaActionAuthority(address,config,profile,log=log)
            authority.status_mode='diagnostic';authority.status_sink='robot' if execute else 'memory'
            authority.status_publisher=Publisher(address,authority.client.session);authority.start()
            start=time.perf_counter();previous=anchor;receipts=[];zero=(0.,)*14
            tx=lambda:{s:int((Path('/sys/class/net')/p/'statistics/tx_packets').read_text()) for s,p in ports.items()}
            trajectory_tx_before=tx()
            if execute:
                # Exclusive consumption prevents repeating a reviewed physical action.
                with args.execute_plan.with_suffix('.consumed').open('x') as claim:claim.write(str(time.time_ns()))
                robot.config.read_only=False
            def halted():return stopped or time.perf_counter()-start>CAPS['max_run_s']
            def dispatch(frozen):
                if not execute:receipts.append(frozen);return
                # Independent immutable envelope, including during the veto test.
                try:validate_target(frozen,anchor,feedback()[0],profile)
                except ValueError as error:raise DispatchNotStarted('commissioning_envelope') from error
                report['actuator_callback_started']+=1
                wrapper.send_action_before(dict(zip(names,frozen)),deadline,stop_requested=halted)
                report['actuator_callback_returned']+=1
            for step,target in enumerate(trajectory(anchor)):
                if halted():raise InterruptedError('Commissioning stopped; no automatic continuation')
                healthy();deadline=time.perf_counter_ns()+config['reply_budget_us']*1000
                dispatch_policy_target(authority,feedback_reader=feedback,
                    cpu_validator=lambda measured:validate_target(target,anchor,measured,profile),
                    dispatch=dispatch,stop_requested=halted,step=step,elapsed_s=time.perf_counter()-start,
                    target=target,previous=previous,act=target,correction=zero,previous_correction=zero,
                    blended=False,queue_depth=0)
                previous=target
                time.sleep(max(0,(step+1)/CAPS['fps']-(time.perf_counter()-start)))
            time.sleep(.2);report['after']=healthy();report['final_feedback']=feedback()[0]
            report['measured_gripper_opening_m']={side:max(r['pose'][i] for r in observations)-anchor[i]
                                                for side,i in zip(('left','right'),GRIPPERS)}
            report['measured_gripper_return_error_m']={side:abs(report['final_feedback'][i]-anchor[i])
                                                     for side,i in zip(('left','right'),GRIPPERS)}
            write_json(args.out/'feedback.json',observations)
            if execute and (report['actuator_callback_returned']!=CAPS['commands']
                    or any(v<.003 for v in report['measured_gripper_opening_m'].values())
                    or any(v>.002 for v in report['measured_gripper_return_error_m'].values())):
                raise RuntimeError('Actual SDK gripper feedback did not verify the commanded small cycle')
            report['trajectory_can_tx_delta']={s:tx()[s]-trajectory_tx_before[s] for s in ports}
            if not execute and any(report['trajectory_can_tx_delta'].values()):
                raise RuntimeError('Unexpected CAN transmission in memory-only trajectory')
            tx_before=tx();callbacks_before=report['actuator_callback_started']
            # Deliberately bypass the CPU validator for a diagnostic copy only.
            # The independent callback envelope also rejects this impossible target.
            bad=list(anchor);bad[8]=2.65;pose,age,sampled=feedback()
            deadline=time.perf_counter_ns()+config['reply_budget_us']*1000
            try:
                authority.send(step=100,elapsed_s=time.perf_counter()-start,target=bad,measured=pose,
                    previous=previous,act=bad,correction=zero,previous_correction=zero,blended=False,
                    feedback_age_us=age,feedback_sampled_ns=sampled,queue_depth=0,dispatch=dispatch,
                    deadline_ns=deadline,stop_requested=halted)
            except AuthorityDenied as error:
                if error.reason!='pl_violation':raise
                report['veto']=dict(error.record)
            else:raise RuntimeError('Expected PL veto was absent')
            sequence=authority.client.sequence
            try:
                authority.send(step=101,elapsed_s=time.perf_counter()-start,target=anchor,measured=pose,
                    previous=previous,act=anchor,correction=zero,previous_correction=zero,blended=False,
                    feedback_age_us=age,feedback_sampled_ns=sampled,queue_depth=0,dispatch=dispatch,
                    deadline_ns=time.perf_counter_ns()+config['reply_budget_us']*1000)
            except AuthorityDenied as error:
                if not error.reason.startswith('latched:'):raise
                report['latched']=True
            else:raise RuntimeError('Expected persistent latch was absent')
            time.sleep(.1);report['veto_can_tx_delta']={s:tx()[s]-tx_before[s] for s in ports}
            if (authority.client.sequence!=sequence or report['actuator_callback_started']!=callbacks_before
                    or any(report['veto_can_tx_delta'].values())):raise RuntimeError('Unexpected dispatch during veto/latch')
            report.update(passed=True,memory_receipts=len(receipts),session=authority.client.session,
                          plan=plan,exit_policy='No further targets, enable, reset, zero or disable; existing servo state retained')
            if not execute:
                path=args.out/'plan.json';write_json(path,plan)
                report['plan_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    except BaseException as error:
        report['error']=type(error).__name__+': '+str(error)
        raise
    finally:
        try:
            if authority:authority.close(outcome='error' if not report['passed'] else 'ended')
        except Exception as error:
            report.update(passed=False,cleanup_error=type(error).__name__+': '+str(error))
        finally:
            if log:log.close()
            if robot:robot.disconnect()
        write_json(args.out/'report.json',report)
        print('BOUNDED_COMMISSIONING='+json.dumps({k:report.get(k) for k in ('passed','execute','error','memory_receipts','actuator_callback_returned','plan_sha256','eligible_for_policy_execution')}),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
