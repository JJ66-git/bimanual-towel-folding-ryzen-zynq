#!/usr/bin/env python3
"""Read-only dual-Piper dashboard check; no policy or motion commands."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
BUNDLE=ROOT/'amd_hybrid_deploy_20261031'
PROJECT=BUNDLE/'piper_towel_replica'
sys.path[:0]=[str(PROJECT/'runtime_source/src'),str(PROJECT/'deployment_entry'),
              str(PROJECT/'scripts'),str(PROJECT/'upstream/act_reference/src'),str(BUNDLE)]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=20)
    parser.add_argument('--check-only',action='store_true')
    args=parser.parse_args()
    if not 1<=args.seconds<=120:parser.error('seconds must be 1..120')
    site=json.loads((PROJECT/'configs/cobot_site.json').read_text())
    print('DASHBOARD_CHECK_CONFIG='+json.dumps(dict(seconds=args.seconds,can=site['robot'],
        cameras=site['camera_indices'],read_only=True,policy_loaded=False,fpga_used=False,
        sdk_queries=True,actuator_commands=False),ensure_ascii=False),flush=True)
    if args.check_only:return 0
    from prepare_cobot import usb_serial
    for side in ('left','right'):
        port=site['robot'][side+'_port']
        try:
            device=(Path('/sys/class/net')/port/'device').resolve(strict=True)
        except FileNotFoundError:
            print(f'DASHBOARD_DEVICE_ERROR: {side} 接口 {port} 不存在；请连接两只 USB-CAN 和三路相机，'
                  '然后运行 ./run.sh prepare --listen-seconds 5。',file=sys.stderr,flush=True)
            return 2
        if usb_serial(device)!=site['usb_serials'][side]:
            print(f'DASHBOARD_DEVICE_ERROR: {side} 接口 {port} 的 USB 身份不匹配；'
                  '请重新运行 ./run.sh prepare --listen-seconds 5。',file=sys.stderr,flush=True)
            return 2
    from lerobot.cameras.opencv import OpenCVCameraConfig
    from lerobot.robots.piper_dual.piper_dual import PIPERDual, PIPERDualConfig
    from shared_runtime.camera_preview import TaskCameraPreview
    cameras={role:OpenCVCameraConfig(index_or_path=site['camera_indices'][role],width=640,height=480,fps=30)
             for role in ('left','middle','right')}
    preview=TaskCameraPreview(title='双臂与相机 · 只读检查')
    robot=None
    count=0
    try:
        preview.start()
        robot=PIPERDual(PIPERDualConfig(left_port=site['robot']['left_port'],
            right_port=site['robot']['right_port'],cameras=cameras,read_only=True))
        robot.connect()
        preview.attach(robot.cameras,robot=robot)
        deadline=time.monotonic()+args.seconds
        print('DASHBOARD_READONLY_ACTIVE: SDK 会发送设备查询，不发送使能或运动目标。',flush=True)
        while time.monotonic()<deadline and not preview.stopping.is_set():
            obs=robot.get_observation()
            preview.update_observation(obs)
            count+=1
            time.sleep(.05)
    except KeyboardInterrupt:
        print('DASHBOARD_READONLY_STOP_REQUESTED',flush=True)
    finally:
        preview.close()
        if robot is not None:
            for camera in robot.cameras.values():
                try:
                    if camera.is_connected:camera.disconnect()
                except Exception as exc:print('CAMERA_CLOSE_ERROR='+str(exc),flush=True)
            for side in ('left','right'):
                try:getattr(robot,side+'_bus').piper.DisconnectPort()
                except Exception as exc:print('CAN_CLOSE_ERROR='+str(exc),flush=True)
    print('DASHBOARD_READONLY_SUMMARY='+json.dumps(dict(observations=count,preview=preview.stats,
          actuator_commands=False),ensure_ascii=False),flush=True)
    return 0 if count else 1


if __name__=='__main__':raise SystemExit(main())
