#!/usr/bin/env python3
"""Offline dashboard demonstration; synthetic data only, no hardware access."""
import argparse
import math
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'amd_hybrid_deploy_20261031/shared_runtime'))
from camera_preview import TaskCameraPreview


class DemoCamera:
    def __init__(self,role):self.role=role
    def read_latest(self,max_age_ms=500):
        img=Image.new('RGB',(640,480),'#18304b')
        draw=ImageDraw.Draw(img)
        draw.rectangle((20,20,620,460),outline='#5dd9e4',width=5)
        draw.text((45,45),'SIMULATED CAMERA / '+self.role.upper(),fill='white')
        x=int(300+170*math.sin(time.monotonic()))
        draw.ellipse((x-28,200,x+28,256),fill='#ffce73')
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


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=30)
    args=parser.parse_args()
    if not 1<=args.seconds<=600:parser.error('seconds must be 1..600')
    preview=TaskCameraPreview(title='演示数据 · 双臂实时状态',fps=5.)
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
            time.sleep(.05)
    finally:
        preview.close()
    print('DASHBOARD_DEMO_COMPLETE',flush=True)


if __name__=='__main__':main()
