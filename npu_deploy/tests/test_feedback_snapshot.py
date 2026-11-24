"""Execute actual motor-read methods without importing or opening hardware SDKs."""
import ast
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
RUNTIME=ROOT/'amd_hybrid_deploy_20261031/piper_towel_replica/runtime_source/src/lerobot'


def actual_robot_methods():
    source=ast.parse((RUNTIME/'robots/piper_dual/piper_dual.py').read_text())
    definition=next(node for node in source.body if isinstance(node,ast.ClassDef) and node.name=='PIPERDual')
    names={'get_motor_observation','get_observation','last_feedback_timestamps'}
    methods=[node for node in definition.body if isinstance(node,ast.FunctionDef) and node.name in names]
    tree=ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='CacheRobot',bases=[],keywords=[],body=methods,decorator_list=[])],type_ignores=[]))
    namespace={'DeviceNotConnectedError':RuntimeError};exec(compile(tree,'actual_piper_read_methods','exec'),namespace)
    return namespace['CacheRobot']


def wrapper_for(robot):
    spec=importlib.util.spec_from_file_location('actual_wrapper_subject',RUNTIME/'rollout/robot_wrapper.py')
    module=importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules,{'lerobot.robots':SimpleNamespace(Robot=object)}):spec.loader.exec_module(module)
    return module.ThreadSafeRobot(robot)


class FeedbackSnapshotTests(unittest.TestCase):
    def test_dispatch_deadline_and_stop_are_checked_after_waiting_for_the_real_lock(self):
        sent=[];self.robot.send_action=lambda action:sent.append(action)
        self.wrapper._lock.acquire()
        try:
            with self.assertRaises(RuntimeError) as error:self.wrapper.send_action_before({'target':1},time.perf_counter_ns()+1_000_000)
            self.assertFalse(error.exception.command_sent)
        finally:self.wrapper._lock.release()
        with self.assertRaisesRegex(RuntimeError,'stop_requested') as error:
            self.wrapper.send_action_before({'target':2},time.perf_counter_ns()+20_000_000,stop_requested=lambda:True)
        self.assertFalse(error.exception.command_sent);self.assertEqual(sent,[])
        self.wrapper.send_action_before({'target':3},time.perf_counter_ns()+20_000_000);self.assertEqual(sent,[{'target':3}])

    def setUp(self):
        self.cameras=[];self.reads=[];outer=self
        class Bus:
            last_feedback_timestamps=None
            def read(self):
                outer.reads.append(True);self.last_feedback_timestamps=dict(source='sdk_monotonic_atomic',joint_monotonic_ns=123,gripper_monotonic_ns=124)
                return {key:value for i in range(1,7) for key,value in ((f'joint_{i}_pos',i/100),(f'joint_{i}_effort',0.))}|dict(gripper_pos=.006,gripper_effort=-.1)
        def camera():outer.cameras.append(True);return 'recorded-frame'
        self.robot=actual_robot_methods()();self.robot._is_connected=True;self.robot.left_bus=Bus();self.robot.right_bus=Bus()
        self.robot.cameras={'middle':SimpleNamespace(async_read=camera)}
        self.wrapper=wrapper_for(self.robot)
    def test_feedback_returns_28_values_and_paired_copied_timestamps_without_camera_access(self):
        observation,times,sampled=self.wrapper.get_feedback_snapshot(timeout=.02)
        self.assertEqual(len(observation),28);self.assertEqual(observation['right_gripper.pos'],.006)
        self.assertEqual(times['left']['joint_monotonic_ns'],123);self.assertGreater(sampled,0)
        self.robot.left_bus.last_feedback_timestamps['joint_monotonic_ns']=0
        self.assertEqual(times['left']['joint_monotonic_ns'],123);self.assertEqual(self.cameras,[]);self.assertEqual(len(self.reads),2)
        full=self.wrapper.get_observation();self.assertEqual(full['middle'],'recorded-frame');self.assertEqual(len(self.cameras),1)
        self.assertEqual({k:v for k,v in full.items() if k!='middle'},observation)
    def test_lock_contention_times_out_before_any_sdk_or_camera_read(self):
        self.wrapper._lock.acquire()
        try:
            with self.assertRaises(TimeoutError):self.wrapper.get_feedback_snapshot(timeout=.001)
        finally:self.wrapper._lock.release()
        self.assertEqual(self.reads,[]);self.assertEqual(self.cameras,[])
    def test_disconnected_read_releases_wrapper_lock(self):
        self.robot._is_connected=False
        with self.assertRaises(RuntimeError):self.wrapper.get_feedback_snapshot(timeout=.02)
        self.assertTrue(self.wrapper._lock.acquire(blocking=False));self.wrapper._lock.release();self.assertEqual(self.reads,[])


if __name__=='__main__':unittest.main()
