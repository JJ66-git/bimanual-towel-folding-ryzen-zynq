import contextlib
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT/'amd_hybrid_deploy_20261031/piper_towel_replica'
for p in (PROJECT/'runtime_source/src', PROJECT/'deployment_entry', PROJECT/'upstream/act_reference/src', ROOT/'amd_hybrid_deploy_20261031/shared_runtime'):
    sys.path.insert(0, str(p))
import cobot_reset as reset


class ResetStopTests(unittest.TestCase):
    def setUp(self):
        reset.stop_requested = False
        reset.awaiting_move_confirmation = False
        self.args = SimpleNamespace(fps=30, joint_speed=.06, gripper_speed=.006, target=None,
            arm='both', execute=True, feedback_warmup=1, left_can='mock_left', right_can='mock_right')
        self.robot = SimpleNamespace(_is_connected=False)

    def invoke(self, answer, move=None):
        output = io.StringIO()
        with contextlib.ExitStack() as stack:
            for attr, val in (('parse_args', lambda:self.args), ('PIPERDual', lambda config:self.robot),
                              ('PIPERDualConfig', lambda **kw:kw), ('validate_pose', lambda *a,**k:None),
                              ('read_stable_positions', lambda *a:reset.TRAINING_START_POSE.copy()),
                              ('read_positions', lambda *a:reset.TRAINING_START_POSE.copy()),
                              ('send_pose', MagicMock()), ('enable_both_holding', MagicMock()),
                              ('hold_measured_pose', MagicMock()), ('move_slowly', move or MagicMock())):
                stack.enter_context(patch.object(reset,attr,val))
            stack.enter_context(patch.object(reset.signal,'signal'))
            stack.enter_context(patch('builtins.input', side_effect=answer if callable(answer) else None,
                                      return_value=None if callable(answer) else answer))
            stack.enter_context(contextlib.redirect_stdout(output))
            try:
                reset.main()
                status = 0
            except SystemExit as exc:
                status = exc.code
            return status, output.getvalue(), reset.send_pose.call_count, reset.hold_measured_pose.call_count

    def test_cancel_does_not_send_or_claim_completion(self):
        status, out, sends, _ = self.invoke('NO')
        self.assertEqual(status,2)
        self.assertEqual(sends,0)
        self.assertNotIn('RESET_EXECUTION_COMPLETE',out)

    def test_interrupt_while_waiting_for_move_exits(self):
        def interrupt(*args): reset.request_stop()
        status, out, sends, _ = self.invoke(interrupt)
        self.assertEqual(status,130)
        self.assertEqual(sends,0)
        self.assertNotIn('RESET_EXECUTION_COMPLETE',out)

    def test_interrupt_during_motion_runs_hold_and_exits(self):
        def interrupt(*args): reset.request_stop()
        status, out, sends, holds = self.invoke('MOVE', move=interrupt)
        self.assertEqual(status,130)
        self.assertEqual(sends,1)
        self.assertEqual(holds,1)
        self.assertNotIn('RESET_EXECUTION_COMPLETE',out)

    def test_one_click_auto_reset_does_not_wait_for_input(self):
        with patch.dict(reset.os.environ, {'AIPC_RESET_AUTO_START': '1'}):
            status, out, sends, holds = self.invoke('NO')
        self.assertEqual(status, 0)
        self.assertEqual(sends, 1)
        self.assertEqual(holds, 1)
        self.assertIn('RESET_AUTO_START', out)
        self.assertIn('RESET_EXECUTION_COMPLETE', out)

    def test_stop_during_feedback_read_does_not_send_next_reset_target(self):
        import numpy as np
        args = SimpleNamespace(fps=30, joint_speed=.06, gripper_speed=.006,
                               tracking_limit=.12, gripper_tracking_limit=.015)
        self.robot.left_bus = object()
        self.robot.right_bus = object()
        def read_and_stop(robot):
            reset.request_stop()
            return np.zeros(14)
        with patch.object(reset, 'read_positions', side_effect=read_and_stop), \
             patch.object(reset, 'arm_is_enabled', return_value=True), \
             patch.object(reset, 'send_pose') as send:
            reset.move_slowly(self.robot, np.zeros(14), np.ones(14)*.01, args)
        send.assert_not_called()

    def test_normal_completion_emits_marker(self):
        status, out, sends, holds = self.invoke('MOVE')
        self.assertEqual(status,0)
        self.assertEqual(sends,1)
        self.assertEqual(holds,1)
        self.assertIn('RESET_EXECUTION_COMPLETE',out)


if __name__ == '__main__': unittest.main()
