"""Daily reconnect: restore identified CAN links and saved camera enumeration.

Uses existing bindings; does not bind new roles, install, load policies, open
camera streams, create SDK robot objects, enable, reset, clear faults or move.
Only SocketCAN network configuration and receive-only feedback are used.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]


def configure_can(port, *, target=None, expected_serial=None):
    target=target or port
    from prepare_cobot import usb_serial
    if expected_serial is not None and usb_serial((Path('/sys/class/net')/port/'device').resolve(strict=True))!=expected_serial:
        raise RuntimeError(f'{port}: USB identity changed before configuration')
    if port!=target and (Path('/sys/class/net')/target).exists():
        raise RuntimeError(f'{target}: target interface name is already occupied')
    before=subprocess.check_output(['ip','-details','link','show',port],text=True)
    # CAN may legitimately have operstate UNKNOWN: inspect the administrative UP flag.
    ready=bool(re.search(r'<[^>]*\bUP\b',before) and re.search(r'\bbitrate\s+1000000\b',before))
    if ready and port==target:
        print(f'CAN {port}: already UP at 1Mbps; unchanged',flush=True)
        return dict(changed=False,port=target,details=before)
    prefix=[] if os.geteuid()==0 else ['sudo']
    # Only the two USB-identified arm adapters reach this function.
    subprocess.run([*prefix,'ip','link','set',port,'down'],check=True)
    if port!=target:
        subprocess.run([*prefix,'ip','link','set',port,'name',target],check=True)
    for command in (['type','can','bitrate','1000000'],['up']):
        subprocess.run([*prefix,'ip','link','set',target,*command],check=True)
    after=subprocess.check_output(['ip','-details','link','show',target],text=True)
    if not (re.search(r'<[^>]*\bUP\b',after) and re.search(r'\bbitrate\s+1000000\b',after)):
        raise RuntimeError(f'{target}: ip configuration did not finish UP at 1Mbps')
    print(f'CAN {port} -> {target}: configured UP at 1Mbps',flush=True)
    return dict(changed=True,previous_port=port,port=target,details=after)


def receive_feedback(ports, seconds):
    import can
    result={side:dict(port=port,rx_frames=0,error_frames=0,status_raw=None,
        joint_enable={},gripper_frames=0,gripper=None) for side,port in ports.items()}
    with ExitStack() as stack:
        buses={side:stack.enter_context(can.Bus(channel=port,interface='socketcan',receive_own_messages=False))
            for side,port in ports.items()}
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            for side,bus in buses.items():
                message=bus.recv(timeout=.01)
                if message is None: continue
                row=result[side]; row['rx_frames']+=1
                if message.is_error_frame:
                    row['error_frames']+=1
                    continue
                data=bytes(message.data)
                if len(data)!=8: continue
                cid=message.arbitration_id
                if cid==0x2A1:
                    row['status_raw']=data.hex()
                elif 0x261<=cid<=0x266:
                    row['joint_enable'][str(cid-0x260)]=bool(data[5]&0x40)
                elif cid==0x2A8:
                    row['gripper_frames']+=1
                    row['gripper']=dict(raw=data.hex(),width_mm=int.from_bytes(data[:4],'big',signed=True)/1000,
                        enabled=bool(data[6]&0x40),fault_bits=hex(data[6]&0x3F),zeroed=bool(data[6]&0x80))
    return result


def update_site(path, before, site):
    if path.read_bytes()!=before:
        raise RuntimeError('Site configuration changed during preparation; not overwriting it')
    if json.loads(before)==site: return False
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                prefix='.cobot_site.',suffix='.tmp',delete=False) as handle:
            temporary=Path(handle.name)
            handle.write(json.dumps(site,indent=2,ensure_ascii=False)+'\n')
        os.chmod(temporary,stat.S_IMODE(path.stat().st_mode))
        if path.read_bytes()!=before:
            raise RuntimeError('Site configuration changed during preparation; not overwriting it')
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists(): temporary.unlink()
    return True


def prepare(root=ROOT, *, discovery=None, can_configurer=None, feedback_reader=None, listen_seconds=2.,check_only=False):
    root=Path(root)
    path=root/'configs/cobot_site.json'
    before=path.read_bytes(); site=json.loads(before)
    if (set(site.get('usb_serials',{}))!={'left','right'}
            or set(site.get('camera_roles',{}))!={'left','middle','right'}
            or not site.get('camera_indices') or not site.get('training_start_pose')
            or not all(site.get('models',{}).get(key) for key in ('act','smolvla'))):
        raise ValueError('Use the already bound Cobot site, not the local unbound template')
    if discovery is None:
        from prepare_cobot import discover
        discovery=discover
    # Resolve all identities BEFORE any network mutation. Never fall back to author port/video numbers.
    ports,cameras=discovery(site)
    if set(ports)!={'left','right'} or set(cameras)!={'left','middle','right'}:
        raise ValueError('Incomplete existing hardware identity mapping')
    if len(set(ports.values()))!=2 or len(set(cameras.values()))!=3:
        raise ValueError('Hardware identities did not resolve to distinct devices')
    targets={side:site['robot'][side+'_port'] for side in ('left','right')}
    if len(set(targets.values()))!=2:
        raise ValueError('Saved left/right CAN names must be distinct')
    # Preflight both names before changing either link; never take over another adapter.
    for side in ('left','right'):
        if targets[side]!=ports[side] and (Path('/sys/class/net')/targets[side]).exists():
            raise RuntimeError(f'{targets[side]}: saved interface name is occupied; no network changes made')
    plan=dict(current_ports=ports,target_ports=targets,camera_indices=cameras,
        usb_serials=site['usb_serials'],models=site['models'],controller_commands_sent=False)
    print('PREPARE_PLAN='+json.dumps(plan),flush=True)
    if check_only:
        print('PREPARE_CHECK_ONLY: identities resolved; no network/configuration changes or actuator commands.',flush=True)
        return plan
    configure=can_configurer or configure_can
    can_results={side:configure(ports[side],target=targets[side],expected_serial=site['usb_serials'][side]) for side in ('left','right')}
    ports=targets
    site['robot'].update(left_port=ports['left'],right_port=ports['right'])
    site['camera_indices']=cameras
    changed=update_site(path,before,site)
    feedback=None
    if listen_seconds>0:
        reader=feedback_reader or receive_feedback
        feedback=reader(ports,listen_seconds)
    now=datetime.now(timezone(timedelta(hours=8)))
    report=dict(timestamp=now.isoformat(),can=can_results,ports=ports,camera_indices=cameras,
        camera_roles=site['camera_roles'],models=site['models'],site_updated=changed,
        feedback=feedback,controller_commands_sent=False,robot_enabled_by_script=False,
        scope='network configuration and current receive-only feedback; NOT policy/task readiness certification')
    folder=root/'logs/preparation'; folder.mkdir(parents=True,exist_ok=True)
    report_path=folder/f'day_{now:%Y%m%d_%H%M%S_%f}_{os.getpid()}.json'
    report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print('BOUND_CAMERAS='+json.dumps(cameras),flush=True)
    print('MODELS_UNCHANGED='+json.dumps(site['models']),flush=True)
    if feedback is not None:
        for side,row in feedback.items():
            if not row['rx_frames']:
                print(f'WARNING {side}: no received frames in this window; interface UP is not feedback availability.',flush=True)
            print('RECEIVE_ONLY '+side+'='+json.dumps(row),flush=True)
        for side,row in feedback.items():
            if row['gripper'] is not None and not row['gripper']['enabled']:
                print(f'INFO {side}: gripper feedback is present but the gripper is disabled; this script does not enable actuators.',flush=True)
    print('PREPARE_REPORT='+str(report_path),flush=True)
    print('PREPARE_DONE: communication configured; no enable/reset/fault-clear/policy commands sent.',flush=True)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--no-feedback',action='store_true',help='Only configure communication/enumeration; skip receive-only sampling')
    p.add_argument('--listen-seconds',type=float,default=2.,help='Receive-only sampling duration, default 2 seconds')
    p.add_argument('--check-only',action='store_true',help='Identify existing CAN/camera roles and print the plan without restoring links')
    args=p.parse_args()
    if not math.isfinite(args.listen_seconds) or args.listen_seconds<=0:
        p.error('listen-seconds must be finite and positive; use --no-feedback to skip')
    if sys.platform!='linux' or sys.version_info[:2]!=(3,12):
        p.error('Run ./run.sh prepare with Linux Python 3.12')
    try:
        prepare(listen_seconds=0 if args.no_feedback else args.listen_seconds,check_only=args.check_only)
    except Exception as exc:
        print(f'PREPARE_FAILED: {type(exc).__name__}: {exc}',file=sys.stderr)
        return 1
    return 0


if __name__=='__main__': raise SystemExit(main())
