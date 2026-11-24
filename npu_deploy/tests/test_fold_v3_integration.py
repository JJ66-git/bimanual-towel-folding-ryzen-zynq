"""Regression cases for real handoff defects; all device/network inputs are simulated."""
import importlib.util
import json
from pathlib import Path
import struct
import sys
import threading
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
DEPLOY=ROOT/'amd_hybrid_deploy_20261031/piper_towel_replica/deployment_entry'
WORKSPACE=ROOT/'fpga_integration/fpga_code/fpga_workspace'
sys.path[:0]=[str(DEPLOY),str(WORKSPACE),str(ROOT/'amd_hybrid_deploy_20261031/piper_towel_replica/runtime_source/src')]
from host import fold_protocol as wire
from host.fold_config import identity as fold_identity, load as load_fold_config
from host.protocol import Sample,UNKNOWN
from host.reference import load_profile,profile_crc
from board.fold_bridge import FoldSession,FoldPynqGuard
from fpga_feedback_age import FeedbackAgeClock
from fpga_fold_observer import FpgaFoldObserver


class WireTests(unittest.TestCase):
    def test_frozen_vectors_and_all_negative_packets(self):
        path=ROOT/'fpga_integration/protocol_fixtures/fpg1_v3_fve1_v1/golden_packets.json'
        fixtures=json.loads(path.read_text())
        for item in fixtures['positive']:
            result=wire.decode(bytes.fromhex(item['hex']), expected_build_id=0x46504734)
            self.assertEqual(list(result.values),item['decoded']['values'],item['name'])
        for item in fixtures['negative']:
            with self.subTest(item=item['name']),self.assertRaises(ValueError):wire.decode(bytes.fromhex(item['hex']), expected_build_id=0x46504734)

    def test_fold_entered_requires_both_valid_and_changed(self):
        for flags in [1,5,9]:
            packet=wire.encode(b'FPG1',3,2,1,2,100,(0,0,0,3,1,3,flags,7,wire.BUILD_ID,UNKNOWN))
            with self.subTest(flags=flags),self.assertRaises(ValueError):wire.decode(packet)

    def test_new_session_restarts_step_and_elapsed(self):
        profile=load_profile();crc=profile_crc(profile)
        class Guard:
            guard_crc=crc
            def check(self,*args,**kwargs):return wire.FPG1_REPLY.pack(crc,0,0,3,1,0,0,7,wire.BUILD_ID,UNKNOWN)
        now=[0.]
        service=FoldSession('127.0.0.1',Guard(),profile,clock=lambda:now[0])
        start=tuple(profile['start']);zeros=(0,)*14
        def packet(session,seq,step,elapsed):
            sample=Sample(crc,step,0 if step==UNKNOWN else 4,elapsed,1000,0,start,start,start,start,zeros,zeros)
            return wire.encode(b'FPG1',3,1,session,seq,seq+1,wire.FPG1_REQUEST.unpack(sample.pack()))
        service.handle(packet(1,0,UNKNOWN,0),('127.0.0.1',10000))
        service.handle(packet(1,1,100,100000),('127.0.0.1',10000))
        now[0]=6.
        service.handle(packet(2,0,UNKNOWN,0),('127.0.0.1',10001))
        service.handle(packet(2,1,0,1),('127.0.0.1',10001))
        self.assertEqual(service.last_step,0)

    def test_b35_snapshot_count_layout(self):
        class IP:
            def read(self,offset):
                return {0x98:fold_identity(load_fold_config())['crc32'],0:wire.BUILD_ID,8:2,0xCC:(1<<31)|(3<<16)|2}.get(offset,0)
            def write(self,*args):pass
        guard=FoldPynqGuard(IP(),load_profile())
        frame=wire.Frame(b'FVE1',1,1,1,2,500,(100,4,4290,300,200,9000,1500,1))
        self.assertEqual(wire.FVE1_RESULT.unpack(guard.feature(frame)),(4,2,0,3))


class FeedbackTests(unittest.TestCase):
    def test_wall_aggregate_is_not_accepted_by_default(self):
        self.assertEqual(FeedbackAgeClock().age_us({'left':{'joint':1,'gripper':1},'right':{'joint':1,'gripper':1}}),UNKNOWN)

    def test_monotonic_oldest_component_and_missing_part(self):
        clock=FeedbackAgeClock(monotonic_ns=lambda:1_000_000_000)
        data={s:dict(source='sdk_monotonic_atomic',joint_monotonic_ns=990_000_000,gripper_monotonic_ns=995_000_000) for s in ['left','right']}
        data['right']['joint_monotonic_ns']=925_000_000
        self.assertEqual(clock.age_us(data),75000)
        data['right']['gripper_monotonic_ns']=None
        self.assertEqual(clock.age_us(data),UNKNOWN)

    def test_sdk_receive_tracks_all_pairs_and_rejects_filtered_refresh(self):
        path=ROOT/'amd_hybrid_deploy_20261031/piper_towel_replica/runtime_source/src/lerobot/motors/piper/feedback_snapshot.py'
        spec=importlib.util.spec_from_file_location('test_feedback_snapshot_impl',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        class Base:
            def __init__(self):self.j=types.SimpleNamespace(time_stamp=0);self.g=types.SimpleNamespace(time_stamp=0)
            def GetArmJointMsgs(self):return self.j
            def GetArmGripperMsgs(self):return self.g
            def ParseCANFrame(self,message):
                if message.accepted:(self.g if message.arbitration_id==0x2A8 else self.j).time_stamp=message.stamp
        instance=module.timestamped_interface(Base)()
        def receive(cid,ns,accepted=True):
            with patch.object(module.time,'monotonic_ns',return_value=ns):
                instance.ParseCANFrame(types.SimpleNamespace(arbitration_id=cid,stamp=ns/1e9,accepted=accepted))
        for i,cid in enumerate([0x2A5,0x2A6,0x2A7,0x2A8]):receive(cid,100+i*10)
        self.assertEqual(instance.feedback_monotonic_snapshot()['joint_monotonic_ns'],100)
        receive(0x2A5,200);receive(0x2A6,210)
        self.assertEqual(instance.feedback_monotonic_snapshot()['joint_monotonic_ns'],120)
        receive(0x2A7,300,False)
        self.assertEqual(instance.feedback_monotonic_snapshot()['joint_monotonic_ns'],120)


class EvidenceTests(unittest.TestCase):
    def test_vision_continues_until_three_frame_terminal_reply(self):
        observer=FpgaFoldObserver.__new__(FpgaFoldObserver)
        observer._channel=None;observer._run_id=None;observer.quality={'segmentation':{}}
        observer._baseline_area=100;observer.calibration_status='synthetic_test'
        observer.cloth_features=lambda *args:dict(area=43,center=[1,1],rectangularity=.9,dark_fraction=.1)
        import numpy as np
        frames=iter([(i,100+i*100000,1.,np.zeros((2,2,3),dtype=np.uint8)) for i in [1,2,3]])
        observer._camera_snapshot=lambda:next(frames)
        results=iter([wire.Frame(b'FVE1',1,2,1,9,1,(1,1,5,1)),wire.Frame(b'FVE1',1,2,1,9,2,(2,1,5,2)),wire.Frame(b'FVE1',1,2,1,9,3,(3,2,0,3))])
        observer.client=types.SimpleNamespace(session=1,send_feature=lambda *args:next(results))
        self.assertEqual([observer._verify(9,100) for _ in range(3)],[False,False,True])

if __name__=='__main__':unittest.main()
