import math
from pathlib import Path
import sys
import time
import unittest
from queue import Queue
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'amd_hybrid_deploy_20261031/shared_runtime'))
import camera_preview as dashboard


def observation():
    result={}
    for side, offset in (('left',0),('right',10)):
        for index in range(1,7):result[f'{side}_joint_{index}.pos']=offset+index/10
        result[f'{side}_gripper.pos']=.012 if side=='left' else .034
    return result


class Camera:
    def __init__(self,fail=False): self.data=np.zeros((4,6,3),dtype=np.uint8); self.fail=fail;self.reads=0
    def read_latest(self,max_age_ms):
        self.reads+=1
        if self.fail:raise TimeoutError('stale')
        return self.data


class Piper:
    def __init__(self,stamp):self.stamp=stamp
    def GetArmJointMsgs(self):return SimpleNamespace(time_stamp=self.stamp)
    def GetArmGripperMsgs(self):return SimpleNamespace(time_stamp=self.stamp)
    def GetArmEndPoseMsgs(self):
        return SimpleNamespace(time_stamp=self.stamp,end_pose=SimpleNamespace(
            X_axis=120001,Y_axis=-2000,Z_axis=3000,RX_axis=90000,RY_axis=-1500,RZ_axis=2000))


class DashboardTests(unittest.TestCase):
    def test_joint_order_units_and_validation(self):
        value=dashboard.capture_joint_state(observation(),12.5)
        self.assertEqual(value['left_joints'],(.1,.2,.3,.4,.5,.6))
        self.assertEqual(value['right_joints'],(10.1,10.2,10.3,10.4,10.5,10.6))
        self.assertAlmostEqual(value['left_gripper_mm'],12.)
        self.assertAlmostEqual(value['right_gripper_mm'],34.)
        bad=observation();bad['left_joint_1.pos']=math.nan
        with self.assertRaises(ValueError):dashboard.capture_joint_state(bad,0.)

    def test_sdk_pose_units_and_missing_timestamp(self):
        bus=SimpleNamespace(piper=Piper(100.))
        result=dashboard.read_sdk_pose(bus)
        self.assertEqual(result['timestamp'],100.)
        self.assertEqual(result['xyz_mm'],(120.001,-2.,3.))
        self.assertEqual(result['rotation_deg'],(90.,-1.5,2.))
        bus.piper.stamp=0.
        self.assertIsNone(dashboard.read_sdk_pose(bus))
        self.assertEqual(dashboard.read_sdk_feedback_times(bus),{'joint':None,'gripper':None})
        self.assertFalse(dashboard.feedback_fresh(None,100.))
        self.assertFalse(dashboard.feedback_fresh(90.,100.))
        self.assertTrue(dashboard.feedback_fresh(99.,100.))

    def test_camera_cache_sampling_does_not_open_or_send(self):
        preview=dashboard.TaskCameraPreview()
        cameras={role:Camera(fail=role=='right') for role in dashboard.ROLES}
        robot=SimpleNamespace(left_bus=SimpleNamespace(piper=Piper(time.time())),
                              right_bus=SimpleNamespace(piper=Piper(time.time())))
        preview.cameras=cameras;preview.robot=robot
        try:
            self.assertTrue(preview.update_observation(observation()))
            self.assertTrue(preview.sample_once())
            cameras['left'].data[:]=255
            packet=preview.frames.get(timeout=2)
            self.assertEqual(set(packet['images']),{'left','middle'})
            self.assertEqual(int(packet['images']['left'][0,0,0]),0)
            self.assertEqual(packet['joints']['left_gripper_mm'],12.)
            self.assertEqual(set(packet['poses']),{'left','right'})
            self.assertEqual(set(packet['feedback_times']),{'left','right'})
        finally:preview.close()

    def test_state_update_never_waits_on_lock(self):
        preview=dashboard.TaskCameraPreview()
        try:
            preview.state_lock.acquire()
            try:self.assertFalse(preview.update_observation(observation()))
            finally:preview.state_lock.release()
            self.assertEqual(preview.stats['state_drops'],1)
        finally:preview.close()

    def test_window_renders_simulated_images_and_state_without_hardware(self):
        import PIL.ImageTk
        widgets=[]
        class Widget:
            def __init__(self,*args,**kwargs): self.kwargs=kwargs; widgets.append(self)
            def __getattr__(self,name):
                if name in ('winfo_screenwidth','winfo_width'): return lambda:1200
                if name in ('winfo_screenheight','winfo_height'): return lambda:800
                return lambda *args,**kwargs:None
            def configure(self,**kwargs): self.kwargs.update(kwargs)
        class Root(Widget):
            def __init__(self): super().__init__(); self.callback=None
            def after(self,delay,callback): self.callback=callback
            def mainloop(self): self.callback()
        class Ready:
            def __init__(self):self.result=None
            def send(self,result):self.result=result
            def close(self):pass
        fake=SimpleNamespace(Tk=Root,Frame=Widget,Label=Widget)
        event=Event();ready=Ready();frames=Queue()
        frames.put(dict(sampled=time.monotonic(),images={r:np.zeros((10,10,3),dtype=np.uint8) for r in dashboard.ROLES},
                        joints=dashboard.capture_joint_state(observation(),time.monotonic()),
                        poses={'left':dashboard.read_sdk_pose(SimpleNamespace(piper=Piper(time.time()))),'right':None},
                        feedback_times={side:dict(joint=time.time(),gripper=time.time()) for side in dashboard.SIDES}))
        rendered=[]
        draw_text=dashboard.render_dashboard_text
        def capture_text(text,*args,**kwargs):
            rendered.append(text)
            return draw_text(text,*args,**kwargs)
        with patch.dict(sys.modules,{'tkinter':fake}), patch.object(PIL.ImageTk,'PhotoImage',return_value=object()), \
             patch.object(dashboard,'render_dashboard_text',side_effect=capture_text):
            dashboard._window(frames,event,ready,'测试')
        self.assertEqual(ready.result,(True,None))
        self.assertTrue(any('J1:' in text for text in rendered))
        self.assertTrue(any('XYZ mm' in text for text in rendered))
        self.assertTrue(any('暂无有效 SDK 反馈' in text for text in rendered))
        self.assertTrue(any('实时状态' in text for text in rendered))

    def test_chinese_is_drawn_with_noto_not_bitmap_placeholder(self):
        first=dashboard.render_dashboard_text('关节', '#ffffff', '#000000', size=18)
        second=dashboard.render_dashboard_text('夹爪', '#ffffff', '#000000', size=18)
        self.assertGreater(np.asarray(first).max(),0)
        self.assertFalse(np.array_equal(np.asarray(first),np.asarray(second)))

if __name__=='__main__':unittest.main()
