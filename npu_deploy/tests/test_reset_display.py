"""Reset receipts are display-only, ordered, expiring and unrelated to folding success."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'amd_hybrid_deploy_20261031/piper_towel_replica/deployment_entry'),
              str(ROOT/'fpga_integration/fpga_code/fpga_workspace')]
from host import task_status
from screen.cooperative_state import CooperativeState
from reset_display import ResetDisplay


class ResetDisplayTests(unittest.TestCase):
    def test_native_panel_receipt_tracks_reset_without_needing_npu_or_camera_preparation(self):
        from showcase_runner import Session
        import json
        session=Session('reset');session._parse_line('Moving slowly. Press Ctrl+C to stop and hold the measured pose.')
        self.assertEqual(session.status.reset_phase,'moving')
        receipt=dict(phase='RESET_DONE',receipts=[dict(phase='RESET_DONE',session=123,sequence=2)],error=None,
                     motion_permission=False,physical_result_verified=False)
        session._parse_line('RESET_DISPLAY='+json.dumps(receipt));self.assertEqual(session.status.reset_phase,'ended')
        self.assertEqual(session.status.reset_display,receipt);self.assertFalse(session.status.npu_inference)
    def test_malformed_reset_receipt_cannot_break_panel_or_claim_completion(self):
        from showcase_runner import Session
        import json
        session=Session('reset')
        for value in ({'phase':'RESET_DONE','receipts':['bad'],'motion_permission':False,'physical_result_verified':False},
                      {'phase':'RESET_DONE','receipts':[],'motion_permission':True,'physical_result_verified':False},[]):
            session._parse_line('RESET_DISPLAY='+json.dumps(value))
        self.assertIsNone(session.status.reset_display)
    def claim(self,phase):return dict(phase=phase,activity='EXECUTING' if phase=='RESETTING' else 'IDLE',origin='HOST')
    def test_reset_cannot_overwrite_active_policy_or_reuse_old_receipts(self):
        receiver=task_status.ResetClaims();address=('127.0.0.1',123)
        with self.assertRaisesRegex(ValueError,'task_active'):
            receiver.accept(1,0,1,self.claim('RESETTING'),address,1,task_active=True)
        receiver.accept(1,0,1,self.claim('RESETTING'),address,1)
        with self.assertRaisesRegex(ValueError,'lease_active'):
            receiver.accept(2,0,2,self.claim('RESETTING'),address,2)
        with self.assertRaisesRegex(ValueError,'old_reset'):
            receiver.accept(1,0,2,self.claim('RESET_DONE'),address,2)
        receiver.accept(1,1,2,self.claim('RESET_DONE'),address,2)
        with self.assertRaisesRegex(ValueError,'old_reset'):
            receiver.accept(1,2,3,self.claim('RESETTING'),address,3)
        receiver.accept(2,0,3,self.claim('RESETTING'),address,3)
    def test_terminal_reset_requires_its_own_start(self):
        receiver=task_status.ResetClaims()
        with self.assertRaisesRegex(ValueError,'start_required'):
            receiver.accept(1,0,1,self.claim('RESET_DONE'),('127.0.0.1',1),1)
    def test_reset_overlays_old_authority_without_certifying_task_or_clearing_internal_latch(self):
        from host.action_status import values
        now=[1_000_000_000];state=CooperativeState('host_observation',clock_ns=lambda:now[0])
        state.update(session=1,authority=values('denied',reason='pl_violation',mask=7),authority_ns=now[0],mask=7)
        state.host_claim(self.claim('RESETTING'),now[0],99);s=state.snapshot()
        self.assertEqual(s['headline'],'双臂正在复位');self.assertEqual(s['task_result'],'unconfirmed')
        self.assertIsNone(s['authority']);self.assertEqual(state.s['authority']['stage'],'denied')
        self.assertIsNone(s['mask']);self.assertEqual(s['guard_label'],'复位沿用原生控制')
        now[0]+=2_600_000_000;self.assertEqual(state.snapshot()['headline'],'复位状态已停更')
        for phase in ('RESET_DONE','RESET_FAILED','RESET_STOPPED'):
            state.host_claim(self.claim(phase),now[0],99);s=state.snapshot()
            self.assertEqual(s['task_result'],'unconfirmed');self.assertFalse(s['visual_success_confirmed'])
            self.assertNotIn('成功',s['headline'])
        state._new_session(2,now[0]);self.assertIsNone(state.snapshot()['maintenance'])
    def test_worker_always_sends_start_before_terminal_even_when_closed_immediately(self):
        calls=[]
        class Client:
            def __init__(self,*args,**kwargs):pass
            def send(self,*values):calls.append(values);return dict(phase=values[0])
            def close(self):pass
        with redirect_stdout(io.StringIO()):
            display=ResetDisplay(('127.0.0.1',1),client_factory=Client);display.close('ended')
        self.assertEqual(calls[0][0],'RESETTING');self.assertEqual(calls[-1][0],'RESET_DONE')
        self.assertFalse(display.thread.is_alive())
    def test_unavailable_display_never_raises_into_motion_controller(self):
        with patch.dict('os.environ',{'AIPC_FPGA_FOLD':'invalid'}),redirect_stdout(io.StringIO()):
            self.assertIsNone(ResetDisplay.from_environment())
        with redirect_stdout(io.StringIO()):
            display=ResetDisplay(('127.0.0.1',1),client_factory=lambda *a,**k:(_ for _ in ()).throw(OSError('offline')))
            display.close('error')
        self.assertIn('OSError',display.error)


if __name__=='__main__':unittest.main()
