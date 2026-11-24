"""Commissioning cannot become an arbitrary learned-policy execution path."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('gripper_commissioning',ROOT/'npu_deploy/fpga_authority/gripper_commissioning.py')
subject=importlib.util.module_from_spec(spec);spec.loader.exec_module(subject)
from host.reference import load_profile


class BoundedCommissioningTests(unittest.TestCase):
    def setUp(self):
        self.profile=load_profile();self.anchor=tuple(v/1e6 for v in self.profile['start'])
        self.board={'implementation_sha256':{'real':'board'}};self.serials={'left':'L','right':'R'}
        self.plan=dict(schema='bounded_gripper_commissioning_plan_v1',anchor=self.anchor,caps=copy.deepcopy(subject.CAPS),
            created_ns=1_000_000,expires_ns=901_000_000,implementation_sha256=subject.identity(),
            boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            board_implementation_sha256=self.board['implementation_sha256'],usb_serials=self.serials)
    def test_fixed_joint_and_five_mm_cycle_returns_to_anchor_at_bounded_speed(self):
        previous=self.anchor;rows=list(subject.trajectory(self.anchor))
        self.assertEqual(len(rows),100);self.assertEqual(rows[-1],self.anchor)
        for target in rows:
            subject.validate_target(target,self.anchor,self.anchor,self.profile)
            for i in subject.JOINTS:self.assertEqual(target[i],self.anchor[i])
            for i in subject.GRIPPERS:
                self.assertLessEqual(abs(target[i]-previous[i])*subject.CAPS['fps'],.0025+1e-12)
            previous=target
        for i in subject.GRIPPERS:self.assertAlmostEqual(max(r[i] for r in rows)-self.anchor[i],.005)
    def test_joint_target_or_gripper_expansion_or_feedback_drift_is_rejected(self):
        for index,delta in ((0,.001),(6,.006),(13,-.001)):
            target=list(self.anchor);target[index]+=delta
            with self.assertRaises(ValueError):subject.validate_target(target,self.anchor,self.anchor,self.profile)
        measured=list(self.anchor);measured[2]+=.021
        with self.assertRaises(ValueError):subject.validate_target(self.anchor,self.anchor,measured,self.profile)
    def test_nonfinite_and_out_of_bounds_anchors_are_rejected_before_motion(self):
        for value in (float('nan'),float('inf'),True):
            pose=list(self.anchor);pose[0]=value
            with self.assertRaises(ValueError):subject.validate_anchor(pose,self.profile)
        pose=list(self.anchor);pose[6]=.079
        with self.assertRaises(ValueError):subject.validate_anchor(pose,self.profile)
    def validate(self,plan=None,pose=None,now_ns=2_000_000):
        return subject.validate_plan(plan or self.plan,pose or self.anchor,self.profile,self.board,self.serials,now_ns=now_ns)
    def test_plan_is_bound_to_boot_code_board_identity_serials_caps_and_age(self):
        self.assertEqual(self.validate(),self.anchor)
        for key,value in (('boot_id','old'),('implementation_sha256',{}),('board_implementation_sha256',{}),
                          ('usb_serials',{}),('caps',{**subject.CAPS,'gripper_open_m':.02})):
            plan=copy.deepcopy(self.plan);plan[key]=value
            with self.assertRaises(ValueError):self.validate(plan)
        for stamp in (0,self.plan['expires_ns']):
            with self.assertRaises(ValueError):self.validate(now_ns=stamp)
    def test_new_feedback_must_remain_within_reviewed_pose(self):
        for index,delta in ((0,.011),(13,.0021)):
            pose=list(self.anchor);pose[index]+=delta
            with self.assertRaises(ValueError):self.validate(pose=pose)


if __name__=='__main__':unittest.main()
