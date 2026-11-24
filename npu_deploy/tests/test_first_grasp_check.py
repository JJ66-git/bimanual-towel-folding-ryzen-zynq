from pathlib import Path
import sys
import unittest


DEPLOY = Path(__file__).resolve().parents[2] / 'amd_hybrid_deploy_20261031/piper_towel_replica/deployment_entry'
sys.path.insert(0, str(DEPLOY))
from first_grasp_check import FirstGraspCheck


class FirstGraspCheckTests(unittest.TestCase):
    def setUp(self):
        self.check = FirstGraspCheck()
        self.tick = 0.0

    def sample(self, target, left_width, right_width, left_effort=.11, right_effort=.55, receipt=None):
        self.tick += .033
        action = [0.0]*14
        action[6] = action[13] = target
        feedback = {
            side: dict(width_mm=width, effort_Nm=effort, fault_bits='0x0', enabled=True,
                       sdk_receipt_timestamp=self.tick+10 if receipt is None else receipt)
            for side, width, effort in [('left', left_width, left_effort), ('right', right_width, right_effort)]
        }
        return self.check.update(self.tick, action, feedback)

    def test_detects_one_side_only_after_first_open_and_settled_closure(self):
        for _ in range(30):
            self.assertFalse(self.sample(.002, 1.5, 1.5))
        self.assertFalse(self.sample(.05, 40, 40))
        for _ in range(20):
            self.assertFalse(self.sample(.002, 1.5, 2.5))
        self.assertEqual(set(self.sample(.002, 1.5, 2.5)), {'left'})
        for _ in range(10):
            self.assertFalse(self.sample(.002, 1.5, 2.5))

    def test_held_cloth_and_reopening_do_not_trigger(self):
        self.sample(.05, 40, 40)
        for _ in range(30):
            self.assertFalse(self.sample(.002, 2.5, 2.5, left_effort=.55))
        self.sample(.05, 40, 40)
        for _ in range(30):
            self.assertFalse(self.sample(.001, 1.5, 1.5, right_effort=.1))
        self.assertTrue(self.check.done)

    def test_stale_receipts_cannot_accumulate_evidence(self):
        self.sample(.05, 40, 40)
        for _ in range(35):
            self.assertFalse(self.sample(.001, 1.5, 1.5, right_effort=.1, receipt=20.0))

    def test_detects_later_load_drop_without_reporting_an_initial_miss(self):
        self.sample(.05, 40, 40)
        for _ in range(21):
            self.assertFalse(self.sample(.001, 3.5, 4.5, left_effort=.38))
        for _ in range(8):
            self.assertFalse(self.sample(.001, 2.2, 4.5, left_effort=.18))
        evidence = self.check.load_drops['left']
        self.assertEqual(evidence['load_observed']['width_mm'], 3.5)
        self.assertEqual(evidence['later_unloaded']['width_mm'], 2.2)
        self.assertNotIn('right', self.check.load_drops)

    def test_transient_contact_does_not_disarm_initial_miss_check(self):
        self.sample(.05, 40, 40)
        for _ in range(13):
            self.assertFalse(self.sample(.001, 8, 4.5))
        for _ in range(4):
            self.assertFalse(self.sample(.001, 3.3, 4.5, left_effort=.38))
        for _ in range(7):
            self.assertFalse(self.sample(.001, 2.2, 4.5, left_effort=.18))
        self.assertIn('left', self.sample(.001, 2.2, 4.5, left_effort=.18))
        self.assertFalse(self.check.load_drops)


if __name__ == '__main__':
    unittest.main()
