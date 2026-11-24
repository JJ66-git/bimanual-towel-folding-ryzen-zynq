"""Synthetic unit fixtures exercise refusal; they are never saved as field evidence."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('qualification_checks',ROOT/'npu_deploy/fpga_authority/qualify.py')
subject=importlib.util.module_from_spec(spec);spec.loader.exec_module(subject)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.identity={'synthetic-unit-test':'not-field-evidence'};self.boot='unit-test-boot'
        self.config=dict(address='192.0.2.1:9009',guard_profile_sha256='unit-test-guard',reply_budget_us=20000,feedback_limit_us=100000)
        names=['joint_'+str(i) for i in range(14)];target=[0.]*14;rows=[];decisions=[];actions=[]
        for i in range(750):
            tick=1_000_000_000+i*33_333_333
            rows.append(dict(step=i,target=target,smol_candidate=target if i>=300 else None))
            decisions.append(dict(event='authority_decision',decision='ALLOW',session=123,sequence=i+1,step=i,
                dispatched=True,violations=0,reference_match=True,build_id=subject.BUILD_ID,feedback_age_us=1000,
                reply_received_ns=tick+1_000_000,dispatch_started_ns=tick+2_000_000,deadline_ns=tick+20_000_000,dispatch_returned_ns=tick+3_000_000))
            actions.append(dict(send_started_monotonic_s=(tick+2_100_000)/1e9,requested=dict(zip(names,target))))
        actions.append(actions[-1].copy())
        plan=dict(stage='full',stop_mode='limit',asset_sha256={'fixture':'hash'},board_implementation_sha256={'fixture':'board'},**self.config)
        service=dict(state='running',source='host_observation',isolated_lcd=True,bulk_vectors=True,implementation_sha256=plan['board_implementation_sha256'])
        self.data=dict(process=dict(motion_requested=True,onsite_operator_confirmed=True,returncode=0),
            trace=dict(execute=True,commissioning=plan,rows=rows,exit_hold=dict(command_returned=True,physical_hold_verified=False),execution_failure=None,termination_reason='action_budget'),
            manifest=dict(execute=True,implementation_sha256=self.identity,boot_id=self.boot,config={'_verified_service':service}),
            decisions=decisions,actions=actions,action_names=names,camera_recording_complete=True,
            onsite_observation=dict(schema='fpga_trial_onsite_observation_v1',session=123,observer_kind='human_onsite',motion_smooth=True,no_interference=True,gripper_observed=True,stop_hold_stable=True),
            console='HANDOFF_REQUEST step=300 NPU_FIRST_INFERENCE_COMPLETED NPU_EXECUTION_CONFIRMED',current_asset_sha256={'fixture':'hash'})
    def check(self,data=None):
        return subject.validate_trial(data or self.data,mode='full',identity=self.identity,boot_id=self.boot,config=self.config)
    def test_complete_unit_fixture_exercises_all_checks_without_writing_a_qualification(self):
        self.assertTrue(self.check()['passed'])
    def test_readonly_receipts_or_partial_controller_cannot_qualify(self):
        for section,key,value in (('trace','execute',False),('manifest','execute',False),('trace','rows',self.data['trace']['rows'][:50])):
            d=copy.deepcopy(self.data);d[section][key]=value;self.assertFalse(self.check(d)['passed'])
    def test_sdk_send_after_its_exact_checked_window_or_changed_target_is_rejected(self):
        d=copy.deepcopy(self.data);d['actions'][0]['send_started_monotonic_s']=2.0
        self.assertIn('dispatch_deadline_missed_or_not_recorded',self.check(d)['errors'])
        d=copy.deepcopy(self.data);d['actions'][0]['requested']['joint_0']=1
        self.assertIn('sdk_target_differs_from_checked_candidate',self.check(d)['errors'])
    def test_missing_human_camera_hold_or_smol_evidence_never_becomes_eligible(self):
        for key,value in (('onsite_observation',{}),('camera_recording_complete',False),('console','')):
            d=copy.deepcopy(self.data);d[key]=value;self.assertFalse(self.check(d)['passed'])
        d=copy.deepcopy(self.data);d['trace']['exit_hold']['command_returned']=False
        self.assertIn('native_exit_hold_missing_or_overclaimed',self.check(d)['errors'])
    def test_stale_code_assets_and_synthetic_board_source_are_rejected(self):
        d=copy.deepcopy(self.data);d['manifest']['implementation_sha256']={}
        self.assertFalse(self.check(d)['passed'])
        d=copy.deepcopy(self.data);d['current_asset_sha256']={}
        self.assertFalse(self.check(d)['passed'])
        d=copy.deepcopy(self.data);d['manifest']['config']['_verified_service']['source']='synthetic_test'
        self.assertIn('unverified_or_wrong_live_board_service',self.check(d)['errors'])


if __name__=='__main__':unittest.main()
