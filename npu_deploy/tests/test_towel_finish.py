from pathlib import Path
import sys
import unittest

import numpy as np

POLICY = Path(__file__).resolve().parents[2] / 'amd_hybrid_deploy_20261031/piper_towel_replica'
sys.path.insert(0, str(POLICY / 'deployment_entry'))
from towel_finish import FirstFoldLandingCheck, TowelFinishCheck


def image(width, height):
    rgb = np.full((480, 640, 3), 210, dtype=np.uint8)
    rgb[210:210+height, 90:90+width] = (35, 105, 160)
    return rgb


class TowelFinishTest(unittest.TestCase):
    def setUp(self):
        self.monitor = TowelFinishCheck(image(420, 180))
        self.first = image(420, 110)
        self.compact = image(220, 135)
        self.count = 0

    def sample(self, rgb, *, opening=0., loaded_width=0., effort=0., extended=False,
               duplicate=False, age=0., held_since=None, right_pose=None, repeat_feedback=False,
               first_fold_retry=False):
        self.count += 1
        now = self.count * .11
        measured = np.zeros(14)
        measured[[1, 8]] = 1.8 if extended else 1.1
        if right_pose is not None:
            measured[[8, 9]] = right_pose
        previous = measured.copy()
        previous[13] = opening
        grip = dict(enabled=True, fault_bits='0x0', width_mm=0., effort_Nm=0.,
                    sdk_receipt_timestamp=self.monitor.last_receipts[0] if repeat_feedback else 1000+now)
        return self.monitor.update(
            rgb, frame_id=self.count-int(duplicate), captured_s=now-age, now_s=now,
            step=self.count, measured=measured, previous=previous,
            grippers={'left': grip, 'right': dict(grip, width_mm=loaded_width, effort_Nm=effort)},
            held_since=held_since, first_fold_retry=first_fold_retry,
        )

    def regrasp(self):
        for _ in range(4):
            self.sample(self.first, extended=True)
        self.sample(self.first, opening=.045, loaded_width=43., extended=True)
        for _ in range(3):
            self.sample(self.first, opening=.002, loaded_width=4., effort=.5, extended=True)
        self.assertIsNotNone(self.monitor.regrasp)

    def test_requires_loaded_regrasp_and_retreat(self):
        for _ in range(4):
            self.sample(self.first, extended=True)
        self.assertIsNotNone(self.monitor.first_fold)
        for _ in range(6):
            self.assertIsNone(self.sample(self.compact))
        self.regrasp()
        for _ in range(6):
            self.assertIsNone(self.sample(self.compact, extended=True))
        for _ in range(4):
            self.assertIsNone(self.sample(self.compact))
        self.assertIsNotNone(self.sample(self.compact))

    def test_stale_and_repeated_frames_do_not_finish_early(self):
        self.regrasp()
        for _ in range(4):
            self.assertIsNone(self.sample(self.compact))
        self.assertIsNone(self.sample(self.compact, duplicate=True))
        self.assertIsNone(self.sample(self.compact, age=.2))
        for _ in range(4):
            self.assertIsNone(self.sample(self.compact))
        self.assertIsNotNone(self.sample(self.compact))

    def test_confirms_near_retreat_after_holding(self):
        self.regrasp()
        for _ in range(6):
            self.assertIsNone(self.sample(self.compact, right_pose=(1.39, -.921)))
        for _ in range(4):
            self.assertIsNone(self.sample(self.compact))
        held_since = self.count * .11
        for _ in range(4):
            self.assertIsNone(self.sample(self.compact, held_since=held_since, right_pose=(1.39, -.921)))
        result = self.sample(self.compact, held_since=held_since, right_pose=(1.39, -.921))
        self.assertTrue(result['confirmed_while_held'])
        self.assertEqual(result['samples'], 5)

    def test_held_confirmation_rejects_bad_shape_or_feedback(self):
        self.regrasp()
        held_since = self.count * .11
        obscured = self.compact.copy()
        obscured[185:210, 65:335] = 0
        for rgb, kwargs in [
            (self.first, {}),
            (obscured, {}),
            (self.compact, {'loaded_width': 4., 'effort': .5}),
            (self.compact, {'right_pose': (1.48, -.921)}),
            (self.compact, {'repeat_feedback': True}),
        ]:
            for _ in range(6):
                self.assertIsNone(self.sample(rgb, held_since=held_since, **kwargs))

    def test_holding_does_not_infer_a_missing_regrasp(self):
        for _ in range(4):
            self.sample(self.first)
        self.assertIsNotNone(self.monitor.first_fold)
        for _ in range(6):
            self.assertIsNone(self.sample(self.compact, held_since=.44))

    def test_first_fold_retry_requires_fresh_consecutive_wide_frames(self):
        wide = image(420, 180)
        for _ in range(5):
            self.sample(wide)
        self.assertIsNone(self.monitor.retry.result)
        self.sample(wide, first_fold_retry=True)
        self.sample(wide, first_fold_retry=True)
        self.sample(wide, age=.2, first_fold_retry=True)
        for _ in range(3):
            self.sample(wide, first_fold_retry=True)
        self.assertIsNone(self.monitor.retry.result)
        self.sample(wide, first_fold_retry=True)
        self.assertEqual(self.monitor.retry.result['samples'], 4)

    def test_first_fold_retry_allows_a_fold_after_the_grasp_miss(self):
        self.sample(image(420, 180), first_fold_retry=True)
        for _ in range(4):
            self.sample(self.first, first_fold_retry=True)
        self.assertIsNotNone(self.monitor.first_fold)
        for _ in range(5):
            self.sample(image(420, 180), first_fold_retry=True)
        self.assertIsNone(self.monitor.retry.result)


class FirstFoldLandingTest(unittest.TestCase):
    def setUp(self):
        self.monitor = FirstFoldLandingCheck()
        self.sample(0., 1., (40., 40.), (42., 42.), (0., 0.))

    def sample(self, now, area, commands, widths, efforts):
        previous = np.zeros(14)
        previous[[6, 13]] = np.asarray(commands) / 1000.
        grips = [dict(width_mm=width, effort_Nm=effort)
                 for width, effort in zip(widths, efforts, strict=True)]
        self.monitor.update(dict(captured_s=now, area_ratio=area, step=round(now*30)), previous, grips)

    def hold(self, right_effort=-.39):
        for now in np.arange(.12, .97, .12):
            self.sample(now, 1.01, (1.8, 1.7), (4.0, 3.4), (-.42, right_effort))

    def open_right(self, now, area):
        self.sample(now, area, (2.2, 5.0), (4.0, 4.6), (-.42, -.09))

    def test_records_still_wide_landing_after_loaded_opening(self):
        self.hold()
        self.open_right(1.08, 1.03)
        self.open_right(1.20, .98)
        self.assertIsNone(self.monitor.incomplete)
        self.open_right(1.32, .94)
        self.assertEqual(self.monitor.incomplete['release']['opening_sides'], ['right'])
        self.assertEqual(self.monitor.incomplete['samples'], 3)

    def test_empty_closure_or_compacting_footprint_does_not_warn(self):
        self.hold(right_effort=-.04)
        for now in (1.08, 1.20, 1.32):
            self.open_right(now, .98)
        self.assertIsNone(self.monitor.release)

        self.setUp()
        self.hold()
        self.open_right(1.08, .92)
        for now in (1.20, 1.32, 1.44):
            self.open_right(now, .70)
        self.assertIsNone(self.monitor.incomplete)

    def test_gap_clears_incomplete_landing_samples(self):
        self.hold()
        self.open_right(1.08, .92)
        self.open_right(1.20, .94)
        self.monitor.gap()
        self.open_right(1.42, .93)
        self.assertIsNone(self.monitor.incomplete)


if __name__ == '__main__':
    unittest.main()
