import contextlib
import importlib.util
import io
from pathlib import Path
import sys
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('dashboard_live_check',ROOT/'npu_deploy/check_dashboard_live.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class ReadOnlyDashboardCheck(unittest.TestCase):
    def test_missing_can_reports_preparation_without_constructing_robot(self):
        fake_prepare=SimpleNamespace(usb_serial=lambda path:'unused')
        error=io.StringIO()
        with patch.dict(sys.modules,{'prepare_cobot':fake_prepare}), \
             patch.object(Path,'resolve',side_effect=FileNotFoundError), \
             patch.object(sys,'argv',['check']), contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(error):
            result=module.main()
        self.assertEqual(result,2)
        self.assertIn('can_left',error.getvalue())
        self.assertIn('prepare --listen-seconds 5',error.getvalue())

    def test_passive_connection_and_cleanup_never_send_actuator_commands(self):
        calls=[]
        class Camera:
            is_connected=True
            def disconnect(self):calls.append('camera_disconnect')
        class Piper:
            def DisconnectPort(self):calls.append('can_disconnect')
        class Robot:
            def __init__(self,config):
                calls.append(('construct',config.read_only))
                self.cameras={name:Camera() for name in ('left','middle','right')}
                self.left_bus=SimpleNamespace(piper=Piper());self.right_bus=SimpleNamespace(piper=Piper())
            def connect(self):calls.append('connect')
            def get_observation(self):calls.append('observe');return {}
        class Preview:
            def __init__(self,**kwargs):self.stopping=Event();self.stats={}
            def start(self):calls.append('window')
            def attach(self,cameras,robot):calls.append('attach')
            def update_observation(self,obs):calls.append('update');self.stopping.set()
            def close(self):calls.append('close')
        def serial(path):
            return 'REPLACE_USB_SERIALS_LEFT' if 'can_left' in str(path) else 'REPLACE_USB_SERIALS_RIGHT'
        modules={
            'prepare_cobot':SimpleNamespace(usb_serial=serial),
            'lerobot.cameras.opencv':SimpleNamespace(OpenCVCameraConfig=lambda **kw:kw),
            'lerobot.robots.piper_dual.piper_dual':SimpleNamespace(PIPERDual=Robot,PIPERDualConfig=lambda **kw:SimpleNamespace(**kw)),
            'shared_runtime.camera_preview':SimpleNamespace(TaskCameraPreview=Preview),
        }
        with patch.dict(sys.modules,modules), patch.object(Path,'resolve',lambda p,strict=False:p), \
             patch.object(sys,'argv',['check']), patch.object(module.time,'sleep'), contextlib.redirect_stdout(io.StringIO()):
            result=module.main()
        self.assertEqual(result,0)
        self.assertIn(('construct',True),calls)
        self.assertEqual(calls.count('observe'),1)
        self.assertEqual(calls.count('camera_disconnect'),3)
        self.assertEqual(calls.count('can_disconnect'),2)

if __name__=='__main__':unittest.main()
