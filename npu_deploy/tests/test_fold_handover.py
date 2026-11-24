from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import numpy as np

POLICY = Path(__file__).resolve().parents[2] / 'amd_hybrid_deploy_20261031/piper_towel_replica'
sys.path.insert(0, str(POLICY / 'deployment_entry'))
from fold_handover import ARMS, FoldHandoverCheck, suitable_fold_view, usable_recovery


def gripper(now, width, effort=0.):
    return dict(enabled=True, fault_bits='0x0', width_mm=width, effort_Nm=effort,
                sdk_receipt_timestamp=1000 + now)


class FoldHandoverTests(unittest.TestCase):
    def setUp(self):
        self.monitor = FoldHandoverCheck()
        self.finish = SimpleNamespace(first_fold={'captured_s': 3.}, regrasp=None,
                                      last_sample=4., right_opened=False)
        self.previous = np.zeros(14)

    def observe(self, now, width, effort, right=None):
        return self.monitor.observe(now, int(now * 30), self.finish, self.previous,
                                    dict(left=gripper(now, width, effort),
                                         right=right or gripper(now, 3.)))

    def test_left_recovery_starts_only_in_the_early_fold_window(self):
        self.assertFalse(self.observe(3.9, 4., -.55))
        self.assertTrue(self.observe(4.04, 4., -.21))
        self.monitor.record_probe(4.04, 121, True, 37.)
        self.assertFalse(self.observe(4.15, 4., -.60))
        self.assertFalse(self.observe(4.8, 4., -.50))

    def test_actual_left_opening_excludes_dual_hold(self):
        self.observe(3.9, 31., .05)
        self.finish.regrasp = {'step': 130}
        self.assertFalse(self.observe(4.2, 3.5, -.62))
        self.assertIsNone(self.monitor.dual_hold)

    def test_distinguishes_dual_hold_from_empty_right_regrasp(self):
        self.finish.regrasp = {'step': 120}
        self.observe(4.2, 3.2, -.60, gripper(4.2, 5., -.36))
        self.assertIsNotNone(self.monitor.dual_hold)
        self.assertIsNone(self.monitor.right_empty)

        self.monitor = FoldHandoverCheck()
        self.finish.regrasp = None
        self.previous[13] = .05
        self.observe(3.7, 0., 0., gripper(3.7, 45.))
        self.previous[13] = 0.
        for now in np.arange(3.8, 4.8, .075):
            self.observe(float(now), 0., 0., gripper(float(now), 1.2, -.08))
        self.assertIsNotNone(self.monitor.right_empty)
        self.assertIsNone(self.monitor.dual_hold)

    def test_candidate_requires_sustained_opening_and_bridges_only_arm_joints(self):
        candidate = np.zeros((50, 14))
        candidate[:, ARMS] = .15
        candidate[16:31, 6] = .037
        candidate[11:40, 13] = .04
        lo, hi = np.full(14, -1.), np.full(14, 1.)
        self.assertTrue(usable_recovery(candidate, self.previous, lo, hi, .85))
        self.monitor.record_probe(4.04, 121, True, 37.)
        shifted = self.monitor.apply_bridge(candidate[16].copy(), self.previous)
        self.assertAlmostEqual(float(shifted[0]), .035)
        np.testing.assert_array_equal(shifted[[6, 13]], candidate[16, [6, 13]])
        short_release_window = candidate.copy()
        short_release_window[23:, 13] = 0.
        self.assertFalse(usable_recovery(short_release_window, self.previous, lo, hi, .85))
        candidate[17:31, 6] = 0.
        self.assertFalse(usable_recovery(candidate, self.previous, lo, hi, .85))

    def test_closing_torque_alone_does_not_confirm_right_grasp(self):
        self.finish.right_opened = True
        for now in np.arange(3.8, 4.95, .075):
            self.observe(float(now), 0., 0., gripper(float(now), 1.1, -.26))
        self.assertIsNotNone(self.monitor.right_unconfirmed)
        self.assertIsNone(self.monitor.right_empty)

    def test_later_loaded_regrasp_resolves_unconfirmed_attempt(self):
        self.finish.right_opened = True
        for now in np.arange(3.8, 4.95, .075):
            self.observe(float(now), 0., 0., gripper(float(now), 7.3, -.10))
        unconfirmed = self.monitor.right_unconfirmed
        self.assertIsNotNone(unconfirmed)
        self.finish.regrasp = {'step': 174}
        self.assertFalse(self.observe(5.8, 0., 0., gripper(5.8, 6.6, -.57)))
        self.assertEqual(self.monitor.right_regrasp_recovered,
                         dict(step=174, unconfirmed_step=unconfirmed['step'], empty_step=None))
        self.observe(5.9, 0., 0., gripper(5.9, 6.6, -.57))
        self.assertEqual(self.monitor.right_regrasp_recovered['step'], 174)

    def test_still_loaded_right_grasp_with_no_sweep_is_advisory(self):
        self.finish.regrasp = {'step': 120}
        measured = np.zeros(14)
        for tick in range(130):
            now = 4. + tick / 30.
            requested = self.monitor.observe(now, tick+120, self.finish, self.previous,
                {'left': gripper(now, .2), 'right': gripper(now, 4.5, .5)},
                measured=measured, time_left_s=4.)
            self.assertFalse(requested)
        evidence = self.monitor.sweep_stalled
        self.assertIsNotNone(evidence)
        self.assertAlmostEqual(evidence['best_progress_mm'], 0.)
        self.assertGreaterEqual(evidence['elapsed_s'], 3.85)

    def test_requires_a_current_long_folded_view(self):
        initial = dict(area=120000., length=570.)
        appearance = dict(area=90000., length=570., rectangularity=.85, solidity=.92)
        self.assertTrue(suitable_fold_view(appearance, initial))
        skewed = dict(appearance, rectangularity=.75, solidity=.84)
        self.assertTrue(suitable_fold_view(skewed, initial))
        self.assertFalse(suitable_fold_view(dict(skewed, solidity=.75), initial))
        self.assertFalse(suitable_fold_view(dict(skewed, area=106000.), initial))
        self.assertFalse(suitable_fold_view(dict(appearance, length=280.), initial))
        self.assertFalse(suitable_fold_view(None, initial))


if __name__ == "__main__":
    unittest.main()
