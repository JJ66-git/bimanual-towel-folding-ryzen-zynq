"""Bounded trials cannot authorize an unreviewed controller or general execution."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch,MagicMock

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'amd_hybrid_deploy_20261031/piper_towel_replica/deployment_entry'),
              str(ROOT/'fpga_integration/fpga_code/fpga_workspace')]
import policy_commissioning as subject
from host.reference import load_profile


class CommissioningTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup);self.root=Path(self.directory.name)
        self.profile=load_profile();self.pose=[v/1e6 for v in self.profile['start']]
        asset=self.root/'asset';asset.write_text('unchanged');self.path=self.root/'plan.json'
        self.args=SimpleNamespace(execute=True,resume_current=False,act_only=False,quality_profile=None,fps=30,seed=42)
        self.plan=dict(schema='fpga_policy_commissioning_plan_v1',stage='small',limits=copy.deepcopy(subject.STAGES['small']),
            anchor=self.pose,stop_mode='limit',created_ns=1,expires_ns=900_000_000_000,implementation_sha256={'host':'known'},
            boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),address='192.0.2.1:9009',
            guard_profile_sha256='guard',reply_budget_us=20000,feedback_limit_us=100000,
            board_implementation_sha256={'board':'known'},eligible_for_policy_execution=False,
            device_bindings={'test':'known'},asset_sha256={str(asset):subject.file_hash(asset)},
            control_settings=subject.settings(self.args),_path=str(self.path))
        self.config={k:self.plan[k] for k in ('address','guard_profile_sha256','reply_budget_us','feedback_limit_us')}
    def load(self,changes=None):
        p={**self.plan,**(changes or {})};self.path.write_text(json.dumps(p))
        with patch.dict(os.environ,{'AIPC_FPGA_COMMISSIONING_PLAN':str(self.path),'AIPC_FPGA_COMMISSIONING_SHA256':subject.file_hash(self.path)}):
            return subject.load_plan({'host':'known'},self.config,now_ns=100,check_bindings=lambda _:True)
    def trial(self):
        t=subject.Trial(self.plan,clock_ns=lambda:100);t.consume();t.check_initial(self.pose);return t
    def test_unqualified_general_execution_is_still_rejected(self):
        from fpga_action_authority import load_execution_config
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(RuntimeError,'not qualified'):
                load_execution_config(ROOT/'npu_deploy/fpga_authority/required.example.json',execute=True)
    def test_digest_missing_or_altered_plan_cannot_be_used(self):
        with patch.dict(os.environ,{'AIPC_FPGA_COMMISSIONING_PLAN':'test'},clear=True):
            with self.assertRaisesRegex(ValueError,'exact SHA'):subject.requested()
        self.path.write_text(json.dumps(self.plan))
        with patch.dict(os.environ,{'AIPC_FPGA_COMMISSIONING_PLAN':str(self.path),'AIPC_FPGA_COMMISSIONING_SHA256':'wrong'}):
            with self.assertRaisesRegex(ValueError,'changed after review'):subject.load_plan({},self.config)
    def test_stale_or_relaxed_plans_and_changed_assets_are_rejected(self):
        self.assertEqual(self.load()['stage'],'small')
        for change in ({'boot_id':'old'},{'implementation_sha256':{}},{'expires_ns':99},
                       {'limits':{**subject.STAGES['small'],'steps':750}},{'eligible_for_policy_execution':True},
                       {'asset_sha256':{str(self.root/'asset'):'wrong'}}):
            with self.subTest(change=change),self.assertRaises(ValueError):self.load(change)
    def test_atomic_one_use_plan_and_initial_pose_limit(self):
        t=subject.Trial(self.plan,clock_ns=lambda:100)
        with self.assertRaises(ValueError):t.check_initial(self.pose)
        t.consume();pose=list(self.pose);pose[0]+=.021
        with self.assertRaisesRegex(ValueError,'Pose moved'):t.check_initial(pose)
        with self.assertRaises((ValueError,FileExistsError)):subject.Trial(self.plan,clock_ns=lambda:100).consume()
        with self.assertRaisesRegex(ValueError,'already consumed'):self.load()
    def test_settings_and_motion_envelopes_are_enforced(self):
        t=self.trial();t.validate_settings(self.args)
        with self.assertRaises(ValueError):t.validate_settings(SimpleNamespace(**{**vars(self.args),'seed':43}))
        def check(target=None,step=0,elapsed=0):
            t.check_target(target or self.pose,self.pose,self.pose,step,elapsed,self.profile)
        check()
        for step,elapsed in ((5,0),(0,11),(0,float('nan'))):
            with self.assertRaises(ValueError):check(step=step,elapsed=elapsed)
        for index,delta in ((0,.151),(6,.011),(8,3)):
            target=list(self.pose);target[index]+=delta
            with self.assertRaises(ValueError):check(target)
    def test_plan_refusal_occurs_before_any_native_connection_hold_or_enable(self):
        from cobot_connection import connect_holding_measured
        robot=SimpleNamespace(config=SimpleNamespace(read_only=False),connect=MagicMock(),cameras={},_is_connected=True)
        with patch('cobot_connection.gripper_snapshot',return_value={}),patch('cobot_connection._measured_pose',return_value=self.pose),\
             patch('cobot_connection._send_hold') as send:
            with self.assertRaisesRegex(ValueError,'refused'):
                connect_holding_measured(robot,before_enable=lambda pose:(_ for _ in ()).throw(ValueError('refused')))
        send.assert_not_called();self.assertFalse(robot._is_connected)
    def test_runner_without_onsite_confirmation_does_not_open_plan_or_start_process(self):
        spec=importlib.util.spec_from_file_location('commissioning_cli_test',ROOT/'npu_deploy/fpga_authority/commissioning.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with patch.object(module.subprocess,'Popen') as child:
            with self.assertRaisesRegex(ValueError,'Onsite observation'):module.run(SimpleNamespace(operator_ready=False))
        child.assert_not_called()
    def test_controlled_fault_uses_the_actual_guard_kernel_and_cannot_call_an_actuator(self):
        from test_authority_adapter import AdapterTests
        from host.action_authority import AuthorityDenied
        for mode,reason in (('pl_veto','pl_violation'),('reply_loss','no_matching_reply')):
            adapter=AdapterTests();adapter.setUp()
            for step in range(5):adapter.run_candidate(step=step)
            trial=subject.Trial({**self.plan,'stop_mode':mode})
            with self.assertRaises(AuthorityDenied) as denied:
                trial.inject_fault(adapter.authority,lambda:(adapter.pose,1000,adapter.now),.2)
            self.assertEqual(denied.exception.reason,reason);self.assertEqual(len(adapter.sent),5)
            self.assertFalse(adapter.authority.gate.records[-1]['dispatched'])


if __name__=='__main__':unittest.main()
