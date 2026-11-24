from pathlib import Path
import sys
import unittest

import numpy as np
from piper_sdk.kinematics.piper_fk import C_PiperForwardKinematics

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "amd_hybrid_deploy_20261031/piper_towel_replica/deployment_entry"))
from first_grasp_approach import MAX_SHIFT_CHANGE_RAD, jaw_pose, plan_arm
from first_grasp_check import WristCornerCheck


class FirstGraspApproachTests(unittest.TestCase):
    def test_batched_pose_matches_sdk_at_regular_and_singular_poses(self):
        poses = np.array([[.111, 2.193, -1.414, .206, .576, -.134],
                          [.25, .088, 0., 0., 0., -.30],
                          [-.20, 0., -1.75, 0., -1.304, .10]])
        sdk = C_PiperForwardKinematics()
        expected = []
        for q in poses:
            x, y, z, roll, pitch, yaw = sdk.CalFK(q)[-1]
            sine_x, sine_y, sine_z = np.sin(np.deg2rad([roll, pitch, yaw]))
            cos_x, cos_y, cos_z = np.cos(np.deg2rad([roll, pitch, yaw]))
            rx = np.array([[1, 0, 0], [0, cos_x, -sine_x], [0, sine_x, cos_x]])
            ry = np.array([[cos_y, 0, sine_y], [0, 1, 0], [-sine_y, 0, cos_y]])
            rz = np.array([[cos_z, -sine_z, 0], [sine_z, cos_z, 0], [0, 0, 1]])
            rotation = rz @ ry @ rx
            expected.append(np.r_[np.array([x, y, z]) + 90 * rotation[:, 2], 45 * rotation.ravel()])
        np.testing.assert_allclose(jaw_pose(poses), expected, rtol=1e-10, atol=1e-10)

    def test_raise_is_bounded_and_returns_to_original_pinch(self):
        pinch = 34
        positions = np.tile([.111, 2.193, -1.414, .206, .576, -.134], (60, 1))
        positions[:pinch, 0] -= np.linspace(.08, .001, pinch)
        widths = np.r_[np.linspace(.055, .016, pinch), np.full(26, .012)]
        lower = np.array([-1.61, -.05, -2.14, -1.58, -1.4, -1.58])
        upper = np.array([1.61, 2.54, .06, 1.58, 1.4, 1.58])
        offsets = plan_arm(positions, widths, 50, True, lower, upper)
        corrected = positions[:50] + offsets
        lift = np.array([jaw_pose(candidate)[2] - jaw_pose(original)[2]
                         for original, candidate in zip(positions[:50], corrected, strict=True)])
        self.assertGreater(lift.max(), 1.0)
        self.assertGreaterEqual(lift.min(), -.05)
        self.assertLessEqual(np.max(np.abs(np.diff(offsets, axis=0))), MAX_SHIFT_CHANGE_RAD + 1e-9)
        np.testing.assert_array_equal(corrected[pinch:], positions[pinch:50])
        no_pinch = plan_arm(positions, np.full(60, .05), 50, True, lower, upper)
        self.assertFalse(no_pinch.any())

    def test_inside_edge_requires_sustained_distinct_wrist_frames(self):
        rgb = np.zeros((480, 640, 3), dtype=np.uint8)
        rgb[205:250, 255:385] = (0, 80, 195)
        feedback = dict(width_mm=44., enabled=True, fault_bits="0x0")
        check = WristCornerCheck()
        self.assertIsNone(check.update("left", rgb, 1, 1., 1.02, 142, .039, feedback))
        self.assertIsNone(check.update("left", rgb, 1, 1., 1.05, 143, .037, feedback))
        observed = check.update("left", rgb, 2, 1.095, 1.11, 145, .033, feedback)
        self.assertEqual(observed["step"], 145)
        self.assertNotIn("right", check.evidence)


if __name__ == "__main__":
    unittest.main()
