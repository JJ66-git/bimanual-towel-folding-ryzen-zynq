#!/usr/bin/env python3
"""Prepare/review bounded learned-policy trials; physical runs require an onsite operator.

`plan` only queries SDK caches/board state. It never enables or sends positions.
`run` consumes one exact plan; it never resets, retries or signs a qualification.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import quote

PROJECT=Path(__file__).resolve().parents[2];BUNDLE=PROJECT/'amd_hybrid_deploy_20261031';POLICY=BUNDLE/'piper_towel_replica'
sys.path[:0]=[str(PROJECT/'npu_deploy'),str(PROJECT/'npu_deploy/fpga_board'),str(POLICY/'deployment_entry'),
              str(POLICY/'runtime_source/src'),str(POLICY/'upstream/act_reference/src'),str(BUNDLE/'shared_runtime')]
from fpga_action_authority import implementation_identity,load_config,load_execution_config,verify_service
from policy_commissioning import STAGES,settings,Trial,file_hash
from operation_lock_path import operation_lock

FIXED_ARGS=['--steps','750','--execute','--trace','--capture-refills','--preview']


def save(path,value):Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def environment():
    env=os.environ.copy()
    for key in ('AIPC_FPGA_SHADOW','AIPC_FPGA_COMMISSIONING_PLAN','AIPC_FPGA_COMMISSIONING_SHA256','AIPC_RESET_AUTO_START'):
        env.pop(key,None)
    env.update(AIPC_FPGA_ADDRESS='192.168.2.99:9009',AIPC_FPGA_FOLD='192.168.2.99:9009',
        AIPC_FPGA_MODE='fpg1_v3',AIPC_FPGA_SOURCE='HOST_OBSERVATION',AIPC_FPGA_REQUIRE_START='1',AIPC_FPGA_AUTO_OFF='0',
        AIPC_FPGA_AUTHORITY_CONFIG=str(PROJECT/'npu_deploy/fpga_authority/required.example.json'),PIPER_ACTION_FILTER_ALPHA='1.0')
    return env


def prepare_service():
    """Reuse a matched service; paired updates take a rollback snapshot first."""
    from jupyter_client import JupyterBoard
    from control_jupyter import request,PROFILE
    board=JupyterBoard();active=json.loads(PROFILE.read_text())
    def call(op,**kwargs):return request(op,board_client=board,**kwargs)
    path=PROJECT/'flexifold_booth_first_grasp/prepare_hardware.py'
    spec=importlib.util.spec_from_file_location('trial_board_prepare',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.deploy(board,active,call)
    started=call('ensure',source='host_observation',isolated_lcd=True,bulk_vectors=True)
    # Only a cold/mode-changing renderer needs this wait. Reused services pass
    # immediately, without repeating cameras, CAN setup or NPU preflight.
    prefix=str(Path(active['board_root']).relative_to('/home/xilinx/jupyter_notebooks'))
    endpoint='/files/'+quote(prefix+'/runs/service_health.json',safe='/');deadline=time.monotonic()+45
    while True:
        health={}
        try:health=board.request('GET',endpoint).json()
        except (OSError,ValueError):pass
        if health.get('pid')==started.get('pid'):
            if health.get('display_error'):raise RuntimeError(health['display_error'])
            if health.get('source')=='host_observation' and health.get('display_ready') is True:break
        if time.monotonic()>deadline:raise TimeoutError('Current FPGA renderer did not become ready')
        time.sleep(.2)
    return call('status')


def plan(args):
    from control_jupyter import request
    from fpga_feedback_age import FeedbackAgeClock
    from lerobot.robots.piper_dual.piper_dual import PIPERDual
    from lerobot.robots.piper_dual.config_piper_dual import PIPERDualConfig
    from lerobot.rollout.robot_wrapper import ThreadSafeRobot
    import cobot_hybrid
    args.out.mkdir(parents=True,exist_ok=False)
    env=environment();os.environ['AIPC_FPGA_FOLD']=env['AIPC_FPGA_FOLD']
    config,profile=load_config(env['AIPC_FPGA_AUTHORITY_CONFIG'])
    command=['bash',str(PROJECT/'npu_deploy/run_hybrid_npu_fpga.sh'),*FIXED_ARGS]
    preview=json.loads(subprocess.check_output(['bash',str(PROJECT/'npu_deploy/run_hybrid_npu.sh'),*FIXED_ARGS,'--check-only'],env=env,cwd=PROJECT,text=True))
    sys.argv=['cobot_hybrid.py',*preview[preview.index('--')+1:]];controller=cobot_hybrid.parse_args()
    site=json.loads((POLICY/'configs/cobot_site.json').read_text())
    bindings={str(Path('/sys/class/net')/site['robot'][s+'_port']/'device'):site['usb_serials'][s] for s in ('left','right')}
    bindings.update({str(Path('/sys/class/video4linux')/('video'+str(site['camera_indices'][s]))/'device'):site['camera_roles'][s] for s in ('left','middle','right')})
    from policy_commissioning import bindings_match
    with operation_lock(PROJECT):
        if not bindings_match(bindings):raise ValueError('Current camera/CAN USB identities differ from site')
        board=prepare_service()
        if not (board.get('state')=='running' and board.get('source')=='host_observation' and board.get('isolated_lcd') is True and board.get('bulk_vectors') is True and board.get('implementation_sha256')):
            raise ValueError('Run the paired optimized board service before preparing a trial')
        robot=PIPERDual(PIPERDualConfig(left_port=site['robot']['left_port'],right_port=site['robot']['right_port'],read_only=True,cameras={}))
        try:
            robot.connect();time.sleep(1);wrapper=ThreadSafeRobot(robot)
            observation,timestamps,sampled_ns=wrapper.get_feedback_snapshot(timeout=.02)
            age=FeedbackAgeClock().age_us(timestamps)
            if age>config['feedback_limit_us']:raise ValueError('Fresh SDK feedback unavailable')
            anchor=[observation[name] for name in profile['action_names']]
        finally:robot.disconnect()
    assets=[POLICY/'configs/cobot_site.json',controller.motion_profile,controller.start_pose_file,
        POLICY/'configs/cobot_observation.json',PROJECT/'npu_deploy/artifacts/smol_vision/smol_vision.onnx',
        PROJECT/'npu_deploy/artifacts/smol_vision/export.json']
    assets += [m/name for m in (controller.act_model,controller.smol_model) for name in ('config.json','model.safetensors')]
    hashes={str(p.resolve()):file_hash(p) for p in assets}
    now=time.perf_counter_ns();p=dict(schema='fpga_policy_commissioning_plan_v1',stage=args.stage,limits=STAGES[args.stage],
        stop_mode=args.stop_mode,anchor=anchor,created_ns=now,expires_ns=now+900_000_000_000,
        implementation_sha256=implementation_identity(),board_implementation_sha256=board['implementation_sha256'],
        boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),address=config['address'],
        guard_profile_sha256=config['guard_profile_sha256'],reply_budget_us=config['reply_budget_us'],
        feedback_limit_us=config['feedback_limit_us'],control_settings=settings(controller),device_bindings=bindings,
        asset_sha256=hashes,eligible_for_policy_execution=False,full_controller_load_measured=False,
        onsite_operator_required=True,requires_new_onsite_confirmation=True,command=command,
        native_connection_and_exit_holds=True,automatic_reset=False,feedback_age_us=age,feedback_sampled_ns=sampled_ns)
    save(args.out/'plan.json',p);digest=file_hash(args.out/'plan.json')
    save(args.out/'review.json',dict(stage=args.stage,limits=p['limits'],stop_mode=args.stop_mode,
        initial_gripper_mm=[anchor[i]*1000 for i in (6,13)],plan_sha256=digest,
        motion_commands_sent=False,expires_after_s=900,eligible_for_policy_execution=False))
    print('COMMISSIONING_PLAN='+json.dumps(dict(plan=str(args.out/'plan.json'),sha256=digest,stage=args.stage,limits=p['limits'],stop_mode=args.stop_mode,motion_commands_sent=False)),flush=True)
    return 0


def run(args):
    if not args.operator_ready:raise ValueError('Onsite observation and emergency stop availability must be confirmed before this physical run')
    env=environment();env.update(AIPC_FPGA_COMMISSIONING_PLAN=str(args.plan.resolve()),AIPC_FPGA_COMMISSIONING_SHA256=args.sha256)
    os.environ.update({k:v for k,v in env.items() if k.startswith('AIPC_FPGA_')})
    config,_=load_execution_config(env['AIPC_FPGA_AUTHORITY_CONFIG'],execute=True);p=config['_commissioning']
    command=['bash',str(PROJECT/'npu_deploy/run_hybrid_npu_fpga.sh'),*FIXED_ARGS]
    if p['command']!=command:raise ValueError('Reviewed command differs from this fixed commissioning entry')
    args.out.mkdir(parents=True,exist_ok=False);child=None;interrupted=False
    report=dict(schema='fpga_policy_commissioning_process_v1',plan=str(args.plan.resolve()),plan_sha256=args.sha256,
        onsite_operator_confirmed=True,motion_requested=True,eligible_for_policy_execution=False,command=command)
    def stop(*_):
        nonlocal interrupted
        interrupted=True
        if child is not None and child.poll() is None:os.killpg(child.pid,signal.SIGINT)
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    with operation_lock(PROJECT):
        prepare_service()
        verify_service(config)
        with (args.out/'console.log').open('x') as log:
            child=subprocess.Popen(command,cwd=PROJECT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            report.update(pid=child.pid,started_ns=time.perf_counter_ns())
            limit=time.monotonic()+p['limits']['max_run_s']+180
            while child.poll() is None:
                if time.monotonic()>limit and not interrupted:stop();report['timeout_stop_requested']=True
                time.sleep(.25)
            report.update(returncode=child.returncode,ended_ns=time.perf_counter_ns(),interrupted=interrupted)
    text=(args.out/'console.log').read_text()
    traces=[line.split('=',1)[1] for line in text.splitlines() if line.startswith('COBOT_TRACE=')]
    if traces:report['trace']=traces[-1]
    recordings=[line.split('=',1)[1] for line in text.splitlines() if line.startswith('OWN_RUN_RECORDING=')]
    if recordings:report['recording']=recordings[-1]
    save(args.out/'process.json',report);print('COMMISSIONING_RUN='+json.dumps(report),flush=True)
    return child.returncode


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='operation',required=True)
    p=sub.add_parser('plan');p.add_argument('--stage',choices=STAGES,default='small')
    p.add_argument('--stop-mode',choices=('limit','operator_stop','pl_veto','reply_loss'),default='limit');p.add_argument('--out',type=Path,required=True)
    p=sub.add_parser('run');p.add_argument('--plan',type=Path,required=True);p.add_argument('--sha256',required=True)
    p.add_argument('--operator-ready',action='store_true');p.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();return plan(args) if args.operation=='plan' else run(args)


if __name__=='__main__':raise SystemExit(main())
