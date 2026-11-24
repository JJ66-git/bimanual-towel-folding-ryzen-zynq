"""Field regression: tiny ACT overshoot must become a legal target, not relaxed bounds."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import unittest
import numpy as np
import torch

ROOT=Path(__file__).resolve().parents[2];POLICY=ROOT/'amd_hybrid_deploy_20261031/piper_towel_replica'
sys.path[:0]=[str(POLICY/'runtime_source/src'),str(POLICY/'deployment_entry'),str(POLICY/'upstream/act_reference/src'),str(ROOT/'amd_hybrid_deploy_20261031/shared_runtime')]
import cobot_hybrid as subject
import blend_core


class ActProjectionTests(unittest.TestCase):
    def setUp(self):
        self.pose=(subject.LOWER+subject.UPPER)/2
        self.pose[subject.GRIPPER]=.04
    def project(self,pose):
        with redirect_stdout(io.StringIO()):
            return subject.as_action(torch.tensor(pose),project_soft_limits=True,projection_label='ACT')
    def test_actual_step_676_overshoot_is_clipped_to_the_original_boundary(self):
        pose=self.pose.copy();pose[1]=-.05140984058380127
        actual=self.project(pose);self.assertEqual(actual[1],subject.LOWER[1]);blend_core.validate_absolute(actual)
        np.testing.assert_array_equal(actual[np.arange(14)!=1],pose[np.arange(14)!=1])
    def test_interior_act_actions_are_numerically_unchanged(self):
        np.testing.assert_array_equal(self.project(self.pose),self.pose)
    def test_larger_invalid_act_is_still_rejected_without_expanding_cpu_or_pl_limits(self):
        pose=self.pose.copy();pose[1]=subject.LOWER[1]-.021
        actual=self.project(pose)
        with self.assertRaises(RuntimeError):blend_core.validate_absolute(actual)
    def test_projection_does_not_mutate_the_model_output_tensor_or_change_gripper_ownership(self):
        pose=self.pose.copy();pose[1]=subject.LOWER[1]-.001;tensor=torch.tensor(pose);before=tensor.clone()
        with redirect_stdout(io.StringIO()):actual=subject.as_action(tensor,project_soft_limits=True,projection_label='ACT')
        self.assertTrue(torch.equal(tensor,before));np.testing.assert_array_equal(actual[subject.GRIPPER],pose[subject.GRIPPER])
    def test_nonfinite_act_is_never_projected_into_a_valid_target(self):
        pose=self.pose.copy();pose[1]=float('nan')
        with self.assertRaises(RuntimeError):self.project(pose)


if __name__=='__main__':unittest.main()
