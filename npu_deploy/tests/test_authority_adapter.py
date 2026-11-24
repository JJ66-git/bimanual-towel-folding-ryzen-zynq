"""Run the actual dispatch adapter with fake feedback, PL replies and actuator."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
DEPLOY=ROOT/'amd_hybrid_deploy_20261031/piper_towel_replica/deployment_entry'
sys.path[:0]=[str(DEPLOY),str(ROOT/'fpga_integration/fpga_code/fpga_workspace')]
import fpga_action_authority as subject
from host.reference import evaluate,load_profile,profile_crc
from host import fold_protocol as wire
from host.action_authority import AuthorityDenied


class AdapterTests(unittest.TestCase):
    def test_countdown_finishes_before_obtaining_the_single_fresh_lease(self):
        instance=subject.FpgaActionAuthority(('127.0.0.1',9009),self.config,self.profile,
            client_factory=self.authority.client.__class__,clock_ns=lambda:self.now)
        order=[];original=instance.client.probe
        def probe(sample):order.append('probe');return original(sample)
        instance.client.probe=probe
        with redirect_stdout(io.StringIO()):instance.start(countdown=5,sleeper=lambda _:order.append('sleep'))
        self.assertEqual(order,['sleep']*5+['probe']);self.assertTrue(instance.ready)
        with self.assertRaisesRegex(RuntimeError,'already started'):instance.start()
    def test_stop_during_startup_countdown_never_probes_or_requests_a_permit(self):
        instance=subject.FpgaActionAuthority(('127.0.0.1',9009),self.config,self.profile,
            client_factory=self.authority.client.__class__,clock_ns=lambda:self.now)
        with self.assertRaises(KeyboardInterrupt):instance.start(countdown=5,stop_requested=lambda:True)
        self.assertEqual(instance.client.sequence,0);self.assertFalse(instance.ready)
    def test_readonly_dispatch_checks_actual_protocol_but_only_reaches_memory(self):
        diagnostics=[]
        self.dispatch=subject.policy_target_dispatch(False,self.dispatch,diagnostics)
        self.run_candidate()
        self.assertEqual(diagnostics,[self.pose]);self.assertEqual(self.sent,[])
        self.assertEqual(self.events,['feedback','cpu','fpga'])
    def test_readonly_denial_never_calls_memory_or_actuator(self):
        diagnostics=[]
        self.dispatch=subject.policy_target_dispatch(False,self.dispatch,diagnostics)
        self.authority.client.drop=True
        with self.assertRaises(AuthorityDenied):self.run_candidate()
        self.assertEqual(diagnostics,[]);self.assertEqual(self.sent,[])
    def test_execute_dispatch_keeps_the_existing_actuator_callback(self):
        diagnostics=[];actuator=self.dispatch
        self.assertEqual(subject.policy_target_dispatch(True,actuator,diagnostics),actuator)
        self.dispatch=subject.policy_target_dispatch(True,actuator,diagnostics);self.run_candidate()
        self.assertEqual(self.sent,[self.pose]);self.assertEqual(diagnostics,[])
    def test_ambiguous_execution_modes_are_rejected(self):
        for mode in ('False',0,1,None):
            with self.assertRaises(ValueError):subject.policy_target_dispatch(mode,self.dispatch,[])
    def test_exit_status_preserves_send_receipt_without_sending_or_certifying_hold(self):
        rows=[];self.authority.status_publisher=SimpleNamespace(publish=lambda row:rows.append(dict(row)))
        self.run_candidate();events=list(self.events)
        with redirect_stdout(io.StringIO()):self.authority.exiting()
        self.assertEqual(self.events,events);self.assertEqual(self.sent,[self.pose])
        self.assertEqual(rows[-1]['stage'],'exiting');self.assertEqual(rows[-1]['outcome'],'sent')
        self.assertIn('尚待确认',subject.action_status.presentation(rows[-1])['summary'])
        self.authority.status_publisher.publish=lambda row:(_ for _ in ()).throw(OSError('display unavailable'))
        with redirect_stdout(io.StringIO()):self.authority.exiting()
        self.assertEqual(self.events,events)
    def test_a_dispatch_lock_denial_latches_as_no_command_instead_of_unknown_send(self):
        class NotStarted(RuntimeError):command_sent=False;reason='dispatch_deadline'
        self.dispatch=lambda target:(_ for _ in ()).throw(NotStarted())
        with self.assertRaisesRegex(AuthorityDenied,'dispatch_deadline'):self.run_candidate()
        self.assertEqual(self.sent,[]);self.assertFalse(self.authority.gate.records[-1]['dispatched'])
        with self.assertRaisesRegex(AuthorityDenied,'latched'):self.run_candidate(step=1)

    def setUp(self):
        self.now=1_000_000_000;self.profile=load_profile();self.sent=[];self.events=[];self.log=io.StringIO()
        self.config=json.loads((ROOT/'npu_deploy/fpga_authority/required.example.json').read_text())
        outer=self
        class Client:
            session=123;sequence=0;timeout=.2;requests=0;closed=False;delay_us=1000;drop=False
            def __init__(self,*args,**kwargs):pass
            def probe(self,sample):self.sequence=1
            def send_sample(self,sample):
                self.requests+=1;outer.events.append('fpga');outer.now+=self.delay_us*1000
                ref=evaluate(sample,outer.profile);seq=self.sequence;self.sequence+=1
                if self.drop:return None
                return wire.Frame(b'FPG1',3,2,123,seq,99,(profile_crc(outer.profile),ref['violations'],ref['events'],ref['phase'],100,0,4,15,wire.BUILD_ID,0xffffffff))
            def close(self):self.closed=True
        self.authority=subject.FpgaActionAuthority(('127.0.0.1',9009),self.config,self.profile,client_factory=Client,clock_ns=lambda:self.now,log=self.log)
        with redirect_stdout(io.StringIO()):self.authority.start()
        self.pose=tuple(v/1e6 for v in self.profile['start']);zero=(0.,)*14
        self.candidate=dict(step=0,elapsed_s=.001,target=self.pose,previous=self.pose,act=self.pose,
                            correction=zero,previous_correction=zero,blended=False,queue_depth=0)
    def feedback(self):self.events.append('feedback');return self.pose,1000,self.now
    def validate(self,measured):self.events.append('cpu');self.assertEqual(measured,self.pose)
    def dispatch(self,target):self.events.append('send');self.sent.append(target)
    def run_candidate(self,**kwargs):
        with redirect_stdout(io.StringIO()):
            return subject.dispatch_policy_target(self.authority,feedback_reader=self.feedback,cpu_validator=self.validate,
                dispatch=self.dispatch,stop_requested=lambda:False,**{**self.candidate,**kwargs})
    def test_feedback_and_cpu_and_fpga_must_all_precede_exactly_one_send(self):
        self.run_candidate();self.assertEqual(self.events,['feedback','cpu','fpga','send']);self.assertEqual(self.sent,[self.pose])
        rows=[json.loads(line) for line in self.log.getvalue().splitlines()]
        self.assertEqual(rows[-1]['event'],'authority_decision');self.assertEqual(rows[-1]['session'],123)
    def test_fpga_veto_latches_and_the_next_candidate_is_not_sent(self):
        bad=list(self.pose);bad[8]=2.65
        with self.assertRaisesRegex(AuthorityDenied,'pl_violation'):self.run_candidate(target=tuple(bad))
        with self.assertRaisesRegex(AuthorityDenied,'latched'):self.run_candidate(step=1)
        self.assertEqual(self.sent,[]);self.assertEqual(self.authority.client.requests,1)
    def test_timeout_cannot_fall_through_to_native_dispatch(self):
        self.authority.client.drop=True
        with self.assertRaisesRegex(AuthorityDenied,'no_matching_reply'):self.run_candidate()
        self.assertEqual(self.sent,[])
    def test_cpu_recheck_failure_does_not_even_request_an_fpga_permit(self):
        self.validate=lambda _:(_ for _ in ()).throw(RuntimeError('CPU rejected fresh feedback'))
        with self.assertRaises(RuntimeError):self.run_candidate()
        self.assertEqual(self.sent,[]);self.assertEqual(self.authority.client.requests,0)
    def test_feedback_refresh_time_is_included_in_the_dispatch_window(self):
        def slow_feedback():self.now+=25_000_000;return self.pose,1000,self.now
        self.feedback=slow_feedback
        with self.assertRaisesRegex(AuthorityDenied,'deadline_before_send'):self.run_candidate()
        self.assertEqual(self.authority.client.requests,0);self.assertEqual(self.sent,[])
    def test_unknown_feedback_and_invalid_candidates_are_not_silently_ignored(self):
        self.feedback=lambda:(self.pose,0xffffffff,self.now)
        with self.assertRaisesRegex(AuthorityDenied,'unknown_feedback_age'):self.run_candidate()
        self.assertEqual(self.sent,[])
        self.setUp()
        with self.assertRaisesRegex(AuthorityDenied,'invalid_candidate'):self.run_candidate(target=(float('nan'),)*14)
        with self.assertRaisesRegex(AuthorityDenied,'latched'):self.run_candidate()
        self.assertEqual(self.sent,[])
    def test_stop_during_reply_wait_is_reported_as_stop_not_success(self):
        with self.assertRaisesRegex(AuthorityDenied,'stop_requested'),redirect_stdout(io.StringIO()):
            subject.dispatch_policy_target(self.authority,feedback_reader=self.feedback,cpu_validator=self.validate,
                dispatch=self.dispatch,stop_requested=lambda:bool(self.authority.client.requests),**self.candidate)
        with patch('host.task_status.Client') as status,redirect_stdout(io.StringIO()):self.authority.close(outcome='stopped')
        status.return_value.send.assert_called_once_with('STOPPED','IDLE','HOST');self.assertEqual(self.sent,[])
    def test_failed_gate_cannot_be_closed_as_a_successful_task(self):
        self.authority.client.drop=True
        with self.assertRaises(AuthorityDenied):self.run_candidate()
        with patch('host.task_status.Client') as status,redirect_stdout(io.StringIO()):self.authority.close(outcome='ended')
        status.return_value.send.assert_called_once_with('FAILED','ERROR','HOST')


class QualificationTests(unittest.TestCase):
    def test_qualification_without_original_model_and_npu_asset_identities_is_rejected(self):
        for report in ({},{'asset_sha256':{}},{'asset_sha256':{'unrelated':'sha'}}):
            with self.assertRaisesRegex(RuntimeError,'assets changed or are missing'):subject.validate_qualified_assets(report)
    def test_matching_acceptance_is_invalidated_by_a_host_implementation_change(self):
        value=json.loads((ROOT/'npu_deploy/fpga_authority/required.example.json').read_text())
        identity=subject.implementation_identity()
        report=dict(schema='fpga_authority_qualification_v1',passed=True,eligible_for_policy_execution=True,
            real_pl=True,build_id=wire.BUILD_ID,implementation_sha256=identity,
            guard_profile_sha256=value['guard_profile_sha256'],reply_budget_us=value['reply_budget_us'],
            feedback_limit_us=value['feedback_limit_us'],fps=30,address=value['address'],
            boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),deadline_misses=0,
            fault_cases_passed=True,full_controller_load_measured=True)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'report.json').write_text(json.dumps(report))
            value['qualification_report']='report.json';(root/'config.json').write_text(json.dumps(value))
            with patch.object(subject,'validate_qualified_assets'):
                config,_=subject.load_config(root/'config.json',execute=True)
            self.assertEqual(config['_qualification']['implementation_sha256'],identity)
            with patch.object(subject,'implementation_identity',return_value={'changed':'implementation'}):
                with self.assertRaisesRegex(RuntimeError,'stale or incomplete'):subject.load_config(root/'config.json',execute=True)

    def test_same_board_implementation_can_restart_but_wrong_mode_or_hash_is_rejected(self):
        import sys
        config={'_qualification':{'board_implementation_sha256':{'server':'known-sha'}}}
        for pid in (100,200):
            state=dict(state='running',source='host_observation',isolated_lcd=True,bulk_vectors=True,
                pid=pid,implementation_sha256={'server':'known-sha'})
            with patch.dict(sys.modules,{'control_jupyter':SimpleNamespace(request=lambda operation:state)}):
                self.assertEqual(subject.verify_service(config)['pid'],pid)
                for changes in ({'source':'synthetic_test'},{'bulk_vectors':False},{'implementation_sha256':{'server':'changed'}}):
                    original=dict(state);state.update(changes)
                    with self.assertRaisesRegex(RuntimeError,'differs from qualification'):subject.verify_service(config)
                    state.clear();state.update(original)

    def test_example_is_deliberately_not_qualified_for_arm_execution(self):
        path=ROOT/'npu_deploy/fpga_authority/required.example.json'
        subject.load_config(path,execute=False)
        with self.assertRaisesRegex(RuntimeError,'not qualified'):subject.load_config(path,execute=True)
    def test_incomplete_or_stale_acceptance_cannot_enable_execution(self):
        value=json.loads((ROOT/'npu_deploy/fpga_authority/required.example.json').read_text())
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'report.json').write_text(json.dumps(dict(passed=True,eligible_for_policy_execution=True,real_pl=True)))
            value['qualification_report']='report.json';(root/'config.json').write_text(json.dumps(value))
            with self.assertRaisesRegex(RuntimeError,'stale or incomplete'):subject.load_config(root/'config.json',execute=True)


if __name__=='__main__':unittest.main()
