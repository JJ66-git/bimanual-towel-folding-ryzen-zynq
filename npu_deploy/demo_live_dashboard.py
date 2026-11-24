#!/usr/bin/env python3
"""Offline dashboard demonstration; synthetic data only, no hardware access."""
import argparse
from dataclasses import replace
import math
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'amd_hybrid_deploy_20261031'))
from shared_runtime.camera_preview import TaskCameraPreview
from shared_runtime.dashboard_telemetry import ControlSnapshot, BoardSnapshot, BoardLink, RunTelemetry, install, uninstall
from shared_runtime.dashboard_view import DashboardState, render_dashboard


class DemoCamera:
    def __init__(self,role):self.role=role
    def read_latest(self,max_age_ms=500):
        img=Image.new('RGB',(640,480),'#25342C')
        draw=ImageDraw.Draw(img)
        # Calibration geometry is intentionally synthetic, never a robot photo.
        for x in range(0,641,80):draw.line((x,0,x,480),fill='#34463B')
        for y in range(0,481,80):draw.line((0,y,640,y),fill='#34463B')
        draw.rectangle((170,110,470,370),outline='#91AD82',width=2)
        draw.line((320,110,320,370),fill='#91AD82',width=1)
        draw.line((170,240,470,240),fill='#91AD82',width=1)
        draw.text((24,22),'SIMULATED / '+self.role.upper(),fill='#E4EADD',font=__import__('shared_runtime.dashboard_view',fromlist=['font']).font(14,mono=True))
        draw.text((24,440),'CALIBRATION PATTERN / NO CAMERA',fill='#A3B49F')
        return np.asarray(img)



class DemoPiper:
    def __init__(self,side):self.side=side
    def GetArmJointMsgs(self):return SimpleNamespace(time_stamp=time.time())
    def GetArmGripperMsgs(self):return SimpleNamespace(time_stamp=time.time())
    def GetArmEndPoseMsgs(self):
        t=time.monotonic()
        return SimpleNamespace(time_stamp=time.time(),end_pose=SimpleNamespace(
            X_axis=int(250000+20000*math.sin(t)),Y_axis=-50000 if self.side=='left' else 50000,
            Z_axis=145000,RX_axis=90000,RY_axis=0,RZ_axis=-10000))


def demo_state(scenario='running', now=None):
    now=time.monotonic() if now is None else now
    state=DashboardState('SIMULATION')
    if scenario=='waiting':return state
    stamp=now-3 if scenario=='stale' else now
    control=ControlSnapshot('demo',stamp,stamp-15.9,step=477,authority=.868,
        max_authority=.9,max_correction_rad=.107,source='SmolVLA',corrected_steps=169,
        dispatched_steps=478,candidate_steps=149,max_actions=750,run_state='running',execute=True,elapsed_s=15.9)
    if scenario=='read-only':control=replace(control,execute=False,corrected_steps=0,dispatched_steps=0,candidate_steps=0)
    if scenario=='zero':control=replace(control,authority=0.,max_correction_rad=0.,source='SAFE')
    if scenario in ('finished','error'):control=replace(control,run_state=scenario)
    packet=dict(sampled=stamp,images={role:DemoCamera(role).read_latest() for role in ('left','middle','right')
        if not (scenario=='dropout' and role=='right')},control=control,
        joints={**{side+'_joints':tuple(.1*math.sin(i) for i in range(6)) for side in ('left','right')},
                'left_gripper_mm':25.,'right_gripper_mm':24.8,'timestamp':stamp},
        poses={},feedback_times={side:dict(joint=time.time(),gripper=time.time()) for side in ('left','right')})
    if scenario.startswith('board-'):
        warning=scenario=='board-warning'
        mismatch=scenario=='board-mismatch'
        received=stamp-3 if scenario=='board-stale' else stamp
        history=tuple(BoardSnapshot(received-(31-i)*.03,0x27A14C82,359+i,444+i,
            not (mismatch and i==31), (1<<3) if warning and i==31 else 0,
            events=1<<3,phase=4,round_trip_ms=2.4+.6*math.sin(i),board_processing_ms=.048,
            checked_count=359+i,reference_matches=359+i-int(mismatch),
            reference_mismatches=int(mismatch),violation_count=int(warning)) for i in range(32))
        packet.update(board=history[-1],board_history=history,
            board_link=BoardLink(('192.0.2.99',9009),0x27A14C82,'D3A0C042',750,750))
    state.ingest(packet)
    state.history.clear()
    for i in range(50):
        a=max(0.,min(.9,.868+.14*math.sin((i-49)/9)+.02*math.sin((i-49)/2.5)))
        if scenario=='zero':a*=min(1.,(49-i)/10)
        if i==49:a=control.authority
        state.history.append((stamp-(49-i)*.2,a))
    return state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=30)
    parser.add_argument('--snapshot',type=Path,help='Export a labeled simulation PNG without opening a window')
    parser.add_argument('--size',default='1520x940')
    parser.add_argument('--scenario',choices=['running','waiting','read-only','stale','finished','error','zero','dropout','board-ok','board-warning','board-mismatch','board-stale'],default='running')
    parser.add_argument('--details',action='store_true')
    parser.add_argument('--page',choices=['cameras','fpga'],default='cameras')
    args=parser.parse_args()
    if not 1<=args.seconds<=600:parser.error('seconds must be 1..600')
    if args.snapshot:
        try:w,h=map(int,args.size.split('x'))
        except ValueError:parser.error('size must be WIDTHxHEIGHT')
        if not 860<=w<=3840 or not 620<=h<=2160:parser.error('size must be within 860x620..3840x2160')
        image,_,_=render_dashboard(demo_state(args.scenario),w,h,details=args.details,page=args.page)
        args.snapshot.parent.mkdir(parents=True,exist_ok=True);image.save(args.snapshot)
        print('DASHBOARD_SIMULATION_SNAPSHOT='+str(args.snapshot));return
    preview=TaskCameraPreview(title='FlexiFold · 模拟演示 / SIMULATION',fps=5.,mode='SIMULATION',page=args.page)
    token=install()
    demo_channel=token[0]
    started=time.monotonic()
    run=RunTelemetry(execute=args.scenario!='read-only',max_actions=750,max_authority=.9)
    step=0
    cameras={name:DemoCamera(name) for name in ('left','middle','right')}
    robot=SimpleNamespace(left_bus=SimpleNamespace(piper=DemoPiper('left')),
                          right_bus=SimpleNamespace(piper=DemoPiper('right')))
    print('DASHBOARD_DEMO: 模拟画面与坐标；不连接相机、机械臂或 FPGA。',flush=True)
    try:
        preview.start(); preview.attach(cameras,robot=robot)
        end=time.monotonic()+args.seconds
        while time.monotonic()<end and not preview.stopping.is_set():
            t=time.monotonic(); sample={}
            for side in ('left','right'):
                for i in range(1,7):sample[f'{side}_joint_{i}.pos']=.25*math.sin(t+i)
                sample[f'{side}_gripper.pos']=.025
            preview.update_observation(sample)
            if args.page=='fpga' or args.scenario.startswith('board-'):
                board_state=demo_state(args.scenario if args.scenario.startswith('board-') else 'board-ok',t)
                count=max(0,step-1)
                history=tuple(replace(board_state.board,sequence=i+1,step=i,checked_count=i+1,
                    reference_matches=i+1-int(not board_state.board.reference_match),
                    reference_mismatches=int(not board_state.board.reference_match))
                    for i in range(max(0,count-32),count))
                demo_channel.board=history[-1] if history else None
                demo_channel.board_history=history
                demo_channel.board_link=board_state.board_link
            if args.scenario=='waiting':demo_channel.control=None
            elif step<750:
                authority=0. if args.scenario=='zero' else max(0.,min(.9,.45+.4*math.sin((t-started)*.7)))
                if args.scenario!='stale' or t-started<2:
                    run.tick(step=step,authority=authority,max_correction_rad=.12*authority,
                        source='SmolVLA' if math.sin((t-started)*.7)>0 else 'SAFE',dispatched=True)
                step+=1
                if args.scenario in ('finished','error'):run.state(args.scenario)
            elif run.value.run_state=='running':run.state('finished')
            time.sleep(.05)
    finally:
        preview.close()
        uninstall(token)
    print('DASHBOARD_DEMO_COMPLETE',flush=True)


if __name__=='__main__':main()
