"""Move part of the ACT approach above the planned first pinch height."""

import numpy as np
from piper_sdk.kinematics.piper_fk import C_PiperForwardKinematics


ARM_SLICES = (slice(0, 6), slice(7, 13))
GRIPPERS = (6, 13)
OPEN_M = .035
PINCH_M = .015
PREGRASP_STEPS = 24
JAW_PROXY_MM = 90.0
MAX_SHIFT_RAD = .045
MAX_SHIFT_CHANGE_RAD = .012
_FK = C_PiperForwardKinematics()
_PERTURBATIONS = 1e-4 * np.eye(6)
_THETA = np.array(_FK._theta)
_COS_ALPHA = np.cos(_FK._alpha)
_SIN_ALPHA = np.sin(_FK._alpha)
_LINKS = np.zeros((6, 4, 4))
_LINKS[:, 0, 3] = _FK._a
_LINKS[:, 1, 2] = -_SIN_ALPHA
_LINKS[:, 1, 3] = -_SIN_ALPHA * _FK._d
_LINKS[:, 2, 2] = _COS_ALPHA
_LINKS[:, 2, 3] = _COS_ALPHA * _FK._d
_LINKS[:, 3, 3] = 1.


def jaw_pose(q):
    """Batch the SDK's modified DH chain for the nominal jaw in mm."""
    theta = np.asarray(q) + _THETA
    cosine, sine = np.cos(theta), np.sin(theta)
    links = np.broadcast_to(_LINKS, theta.shape[:-1] + _LINKS.shape).copy()
    links[..., 0, 0] = cosine
    links[..., 0, 1] = -sine
    links[..., 1, 0] = sine * _COS_ALPHA
    links[..., 1, 1] = cosine * _COS_ALPHA
    links[..., 2, 0] = sine * _SIN_ALPHA
    links[..., 2, 1] = cosine * _SIN_ALPHA
    flange = links[..., 0, :, :]
    for index in range(1, 6):
        flange = flange @ links[..., index, :, :]
    rotation = flange[..., :3, :3].copy()
    # Preserve the SDK's Euler convention around the two singularities.
    pitch_axis = rotation[..., 2, 0]
    singular = np.abs(pitch_axis) > .9999
    if np.any(singular):
        direction = -np.sign(pitch_axis[singular])
        roll = direction * np.arctan2(rotation[..., 0, 1][singular],
                                        rotation[..., 1, 1][singular])
        sr, cr = np.sin(roll), np.cos(roll)
        zero = np.zeros_like(sr)
        rotation[singular] = np.stack((zero, direction * sr, direction * cr,
                                       zero, cr, -sr, -direction, zero, zero), axis=-1).reshape(-1, 3, 3)
    jaw = flange[..., :3, 3] + JAW_PROXY_MM * rotation[..., :, 2]
    return np.concatenate((jaw, 45.0 * rotation.reshape(theta.shape[:-1] + (9,))), axis=-1)


def _translate(q, pose, translation):
    desired = pose.copy()
    desired[:3] += translation
    result = q.copy()
    for iteration in range(2):
        current = jaw_pose(result)
        jacobian = (jaw_pose(result + _PERTURBATIONS) - current).T / 1e-4
        try:
            correction = np.linalg.lstsq(jacobian, desired - current, rcond=None)[0]
        except np.linalg.LinAlgError:
            return q
        largest = np.max(np.abs(correction))
        if iteration == 0 and largest > .9 * MAX_SHIFT_RAD:
            scale = .9 * MAX_SHIFT_RAD / largest
            correction *= scale
            desired = pose + (desired - pose) * scale
        result += correction
        if not np.isfinite(result).all() or np.max(np.abs(result - q)) > MAX_SHIFT_RAD:
            return q
    if np.max(np.abs(jaw_pose(result) - desired)) > .15:
        return q
    return result


def _ease(value):
    value = np.clip(value, 0, 1)
    return value * value * (3.0 - 2.0 * value)


def _low_travel(poses, reference_height):
    lateral = np.linalg.norm(np.diff(poses[:, :2], axis=0), axis=1)
    near_pinch = np.maximum(poses[:-1, 2], poses[1:, 2]) <= reference_height + 5.0
    return float(lateral[near_pinch].sum())


def plan_arm(positions, widths, prefix, seen_open, lower, upper):
    """Return joint offsets for the executable prefix of a predicted ACT chunk."""
    offsets = np.zeros((prefix, 6))
    opened = seen_open or np.any(widths[:prefix] >= OPEN_M)
    if not opened:
        return offsets
    opened_by_step = np.maximum.accumulate(widths >= OPEN_M) | seen_open
    pinches = np.flatnonzero(opened_by_step & (widths < PINCH_M))
    if not pinches.size:
        return offsets
    pinch = int(pinches[0])
    begin, end = max(0, pinch - PREGRASP_STEPS), min(prefix - 1, pinch - 2)
    if end - begin < 8:
        return offsets
    indices = np.arange(begin, end)
    goal = jaw_pose(positions[pinch])[:3]
    poses = jaw_pose(positions[indices])
    remaining = goal[:2] - poses[:, :2]
    distances = np.linalg.norm(remaining, axis=1)
    if distances.max() < 6.0:
        return offsets
    # On the first tick of a refill the previous command already exists. Use
    # the correction slew bound to allow a small raise at that boundary.
    ramp_in = _ease((indices - begin + (1 if begin == 0 else 0)) / 4.0)
    fade = ramp_in * _ease((end - indices) / 4.0)
    clearance = 8.0 * _ease((distances - 1.0) / 4.0)
    lift = fade * np.clip(goal[2] + clearance - poses[:, 2], 0, 8)
    for index, pose, sideways, rise, weight in zip(indices, poses, remaining, lift, fade, strict=True):
        if rise < .5:
            continue
        xy = np.clip(sideways * .2 * weight, -3.0, 3.0)
        original = positions[index]
        shifted = _translate(original, pose, np.array((*xy, rise)))
        if np.all((shifted >= lower) & (shifted <= upper)):
            offsets[index] = shifted - original
    offset_step = max(np.max(np.abs(offsets[0])), np.max(np.abs(np.diff(offsets, axis=0))))
    if offset_step > MAX_SHIFT_CHANGE_RAD:
        # Scale the whole correction so the seam and the planned pinch remain continuous.
        offsets *= MAX_SHIFT_CHANGE_RAD / offset_step
    if np.any(offsets):
        comparison = positions[begin:end + 1]
        original = jaw_pose(comparison)
        adjusted = jaw_pose(comparison + offsets[begin:end + 1])
        # Keep the unmodified plan when the nominal approach is already better.
        if (_low_travel(adjusted, goal[2]) >= _low_travel(original, goal[2]) - .3
                or np.min(adjusted[:, 2] - original[:, 2]) < -.05):
            offsets.fill(0)
    return offsets


class FirstGraspApproach:
    """Use the full ACT lookahead while preserving its native executed prefix."""

    def __init__(self, postprocessor, prefix, lower, upper, alpha):
        self.postprocessor = postprocessor
        self.prefix = prefix
        self.lower = lower
        self.upper = upper
        self.alpha = alpha
        self.opened = [False, False]
        self.pinched = [False, False]
        self.offsets = np.zeros((prefix, 14))
        self.index = prefix
        self.applied_ticks = 0
        self.previous_base = None

    def on_refill(self, prediction):
        self.offsets.fill(0)
        self.index = 0
        if not any(opened and not closed for opened, closed in zip(self.opened, self.pinched, strict=True)):
            return
        actions = self.postprocessor(prediction).detach().float().cpu().numpy()
        if actions.ndim != 3 or actions.shape[0] != 1 or actions.shape[2] != 14:
            return
        actions = actions[0]
        if len(actions) < self.prefix or self.previous_base is None or not np.isfinite(actions).all():
            return
        for arm, joints, gripper in zip(range(2), ARM_SLICES, GRIPPERS, strict=True):
            if self.opened[arm] and not self.pinched[arm]:
                planned = np.empty_like(actions[:, joints])
                previous = self.previous_base[joints]
                for index, joints_target in enumerate(actions[:, joints]):
                    previous = previous + self.alpha * (joints_target - previous)
                    planned[index] = previous
                self.offsets[:, joints] = plan_arm(planned, actions[:, gripper],
                                                   self.prefix, True, self.lower[joints], self.upper[joints])

    def apply(self, target):
        self.previous_base = target.copy()
        offset = self.offsets[self.index].copy() if self.index < self.prefix else np.zeros(14)
        self.index += 1
        for arm, joints, gripper in zip(range(2), ARM_SLICES, GRIPPERS, strict=True):
            self.opened[arm] |= target[gripper] >= OPEN_M
            self.pinched[arm] |= self.opened[arm] and target[gripper] < PINCH_M
            if self.pinched[arm]:
                offset[joints] = 0.0
        if not np.any(offset):
            return target
        shifted = (target + offset).astype(target.dtype)
        if not np.all((shifted >= self.lower) & (shifted <= self.upper)):
            return target
        self.applied_ticks += 1
        return shifted
