"""Feedback checks and an ACT recovery candidate before the second fold."""

import itertools
import math

import numpy as np

from first_grasp_check import GripperCheck
from fold_sweep_feedback import SweepMonitor, loaded_grip
from towel_finish import usable_gripper


LEFT, RIGHT = 6, 13
ARMS = np.array((0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12))
BRIDGE_STEPS = 12
BRIDGE_STEP_RAD = .035


def longest_open(widths, minimum=.025):
    return max((sum(1 for _ in run) for is_open, run in itertools.groupby(widths >= minimum) if is_open),
               default=0)


def suitable_fold_view(shape, initial):
    # A skewed first fold can leave an irregular long edge. This gate is
    # reached after a confirmed first fold and while the left claw is loaded.
    # A shorter silhouette remains ineligible for the regrasp recovery.
    return (shape is not None and initial is not None
            and .48 <= shape["area"] / initial["area"] <= .83
            and .86 <= shape["length"] / initial["length"] <= 1.12
            and shape["rectangularity"] >= .72 and shape["solidity"] >= .82)


def usable_recovery(actions, previous, lower, upper, alpha, fps=30.):
    """Accept a bounded ACT chunk that leaves time to release before regrasp."""
    if actions.shape != (50, 14) or not np.isfinite(actions).all():
        return False
    if not np.all((actions[:, ARMS] >= lower[ARMS]) & (actions[:, ARMS] <= upper[ARMS])):
        return False
    if longest_open(actions[4:, LEFT]) < 10 or longest_open(actions[:, RIGHT]) < 6:
        return False
    left_opens = np.flatnonzero(actions[:, LEFT] >= .025)[0]
    if left_opens < 4 or left_opens > 38:
        return False
    right_opened = np.maximum.accumulate(actions[:, RIGHT] >= .025)
    right_recloses = np.flatnonzero(right_opened & (actions[:, RIGHT] < .012))
    release_lead = math.ceil(.30 * fps)
    if right_recloses.size and right_recloses[0] < left_opens + release_lead:
        return False
    joints = previous[ARMS]
    if np.max(np.abs(actions[0, ARMS] - joints)) > .18:
        return False
    for i, target in enumerate(actions[:, ARMS]):
        delta = alpha * (target - joints)
        if i < BRIDGE_STEPS:
            delta = np.clip(delta, -BRIDGE_STEP_RAD, BRIDGE_STEP_RAD)
        if np.max(np.abs(delta)) > .08:
            return False
        joints = joints + delta
    return True


class FoldHandoverCheck:
    def __init__(self):
        self.left_opened = False
        self.left_loaded_s = None
        self.last_receipts = (0., 0.)
        self.last_probe = -float("inf")
        self.probes = []
        self.bridge_remaining = 0
        self.right_check = GripperCheck()
        self.right_closed_s = None
        self.right_sample_s = None
        self.dual_hold = None
        self.right_empty = None
        self.right_unconfirmed = None
        self.right_regrasp_recovered = None
        self.sweep_monitor = None
        self.sweep_stalled = None

    def observe(self, now, step, finish, previous, feedback, *, measured=None, time_left_s=0.):
        if finish.first_fold is None:
            return False
        left, right = (feedback.get(side, {}) for side in ("left", "right"))
        if not all(map(usable_gripper, (left, right))):
            return False
        receipts = left["sdk_receipt_timestamp"], right["sdk_receipt_timestamp"]
        if any(stamp <= last for stamp, last in zip(receipts, self.last_receipts, strict=True)):
            return False
        self.last_receipts = receipts
        self.left_opened |= left["width_mm"] >= 25.
        left_loaded = (previous[LEFT] < .009 and left["width_mm"] >= 2.5
                       and left["width_mm"] - 1000 * previous[LEFT] >= 1.0
                       and abs(left["effort_Nm"]) >= .30)
        if left_loaded:
            self.left_loaded_s = now
        if finish.regrasp is not None:
            if self.right_regrasp_recovered is None and (self.right_unconfirmed or self.right_empty):
                self.right_regrasp_recovered = dict(
                    step=finish.regrasp["step"],
                    unconfirmed_step=self.right_unconfirmed["step"] if self.right_unconfirmed else None,
                    empty_step=self.right_empty["step"] if self.right_empty else None,
                )
            if not self.left_opened and left_loaded and self.dual_hold is None:
                self.dual_hold = dict(step=step, right_regrasp_step=finish.regrasp["step"],
                                      left_width_mm=left["width_mm"], left_effort_Nm=left["effort_Nm"])
            if measured is not None:
                self.observe_right_sweep(now, step, measured, previous, left, right, time_left_s)
            return False
        sample_gap = self.right_sample_s is None or now - self.right_sample_s > .18
        self.right_sample_s = now
        if finish.right_opened and previous[RIGHT] < .012:
            if self.right_closed_s is None or sample_gap:
                self.right_closed_s = now
            if now - self.right_closed_s >= .90 and self.right_unconfirmed is None:
                self.right_unconfirmed = dict(step=step, width_mm=right["width_mm"],
                                              effort_Nm=right["effort_Nm"])
        else:
            self.right_closed_s = None
        unloaded = self.right_check.update(now, previous[RIGHT], right)
        if unloaded is not None and self.right_empty is None:
            self.right_empty = dict(step=step, **unloaded)
        age = now - finish.first_fold["captured_s"]
        return (not self.left_opened and self.left_loaded_s is not None
                and now - self.left_loaded_s <= .40 and previous[LEFT] < .009
                and 2.5 <= left["width_mm"] <= 9.0 and not finish.right_opened
                and 1.015 <= age <= 1.52 and now - self.last_probe >= .11
                and len(self.probes) < 3 and not any(item["accepted"] for item in self.probes)
                and finish.last_sample is not None and now - finish.last_sample <= .16)

    def observe_right_sweep(self, now, step, measured, previous, left, right, time_left_s):
        """Log stalled motion using Piper local Y and measured grip load."""
        monitor = self.sweep_monitor
        if self.sweep_stalled is not None or (monitor is not None and monitor.released):
            return
        from first_grasp_approach import jaw_pose
        valid = np.isfinite(measured[7:13]).all()
        position = float(jaw_pose(measured[7:13])[1]) if valid else float("nan")
        if monitor is None:
            if not valid:
                return
            monitor = self.sweep_monitor = SweepMonitor(now, position)
        right_command_mm, left_command_mm = previous[[RIGHT, LEFT]] * 1000.
        evidence = monitor.observe(
            now, position, valid=valid, time_left_s=time_left_s,
            right_loaded=loaded_grip(right, right_command_mm),
            left_loaded=loaded_grip(left, left_command_mm, minimum_width_mm=2.5),
            right_released=right_command_mm >= 14. and right["width_mm"] >= 15.,
        )
        if evidence is not None:
            self.sweep_stalled = dict(step=step, **evidence)

    def record_probe(self, now, step, accepted, peak_mm):
        self.last_probe = now
        self.probes.append(dict(step=step, accepted=accepted, left_peak_mm=peak_mm))
        if accepted:
            self.bridge_remaining = BRIDGE_STEPS

    def apply_bridge(self, target, previous):
        if self.bridge_remaining:
            self.bridge_remaining -= 1
            target[ARMS] = np.clip(target[ARMS], previous[ARMS] - BRIDGE_STEP_RAD,
                                   previous[ARMS] + BRIDGE_STEP_RAD)
        return target
