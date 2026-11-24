"""Exit holds use paired motor feedback, bounded sends and truthful receipts."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'amd_hybrid_deploy_20261031/piper_towel_replica/deployment_entry'))
from policy_exit import hold_current


class ExitTests(unittest.TestCase):
    def setUp(self):
        self.calls=[];self.age=1000
        self.pose=[.01]*14
        self.driver={s:dict(joint_enabled=[True]*6,enabled=True,fault_bits='0x0') for s in ('left','right')}
        self.wrapper=SimpleNamespace(get_feedback_snapshot=lambda **k:(dict(pose=self.pose),{},1),
            send_action_before=lambda action,deadline:self.calls.append((action,deadline)))
    def run_hold(self):
        return hold_current(self.wrapper,lambda o:o['pose'],lambda p:dict(action=p),lambda:copy.deepcopy(self.driver),
            age_clock=SimpleNamespace(age_us=lambda _:self.age),clock_ns=lambda:1000)
    def test_returned_native_send_never_claims_physical_hold_or_fpga_approval(self):
        r=self.run_hold();self.assertTrue(r['command_returned']);self.assertEqual(len(self.calls),1)
        self.assertFalse(r['physical_hold_verified']);self.assertFalse(r['fpga_gated'])
        self.assertEqual(self.calls[0][0]['action'],self.pose)
    def test_stale_or_unknown_feedback_does_not_issue_a_hold(self):
        for age in (100001,0xffffffff):
            self.age=age;r=self.run_hold();self.assertFalse(r['command_started']);self.assertEqual(self.calls,[])
    def test_disabled_or_faulted_drivers_are_not_reenabled_by_exit(self):
        for key,value in (('enabled',False),('fault_bits','0x1'),('joint_enabled',[False]*6)):
            original=copy.deepcopy(self.driver);self.driver['left'][key]=value
            self.assertFalse(self.run_hold()['command_started']);self.assertEqual(self.calls,[]);self.driver=original
    def test_feedback_lock_timeout_does_not_fall_back_to_old_candidate(self):
        self.wrapper.get_feedback_snapshot=lambda **k:(_ for _ in ()).throw(TimeoutError('busy'))
        self.assertFalse(self.run_hold()['command_started']);self.assertEqual(self.calls,[])
    def test_sdk_failure_is_unknown_and_never_retried(self):
        def fail(*args):self.calls.append(args);raise OSError('unknown send result')
        self.wrapper.send_action_before=fail;r=self.run_hold()
        self.assertTrue(r['command_started']);self.assertFalse(r['command_returned']);self.assertEqual(len(self.calls),1)
    def test_dispatch_deadline_rejection_is_reported_as_not_sent(self):
        class Rejected(RuntimeError):command_sent=False
        self.wrapper.send_action_before=lambda *a:(_ for _ in ()).throw(Rejected('expired'))
        r=self.run_hold();self.assertFalse(r['command_started']);self.assertFalse(r['command_returned'])


if __name__=='__main__':unittest.main()
