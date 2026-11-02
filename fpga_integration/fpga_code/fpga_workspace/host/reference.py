"""Integer golden model for the proposed checker, not a production motion gate."""
import json
from pathlib import Path
import zlib

from .protocol import ACT, BLEND, CHECK_CORRECTION, CHECK_START, MANUAL_HANDOFF, RUNNING, UNKNOWN

ARM = tuple(i for i in range(14) if i not in (6, 13))
NAMES = ('target_bounds', 'act_bounds', 'joint_step', 'tracking', 'correction_magnitude',
         'correction_slew', 'gripper_ownership', 'start_pose', 'action_budget',
         'feedback_stale', 'profile_mismatch')


def load_profile(path=None):
    path = Path(path) if path else Path(__file__).resolve().parents[1] / 'config/guard_profile.json'
    return json.loads(path.read_text())


def profile_crc(profile):
    canonical = json.dumps(profile, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return zlib.crc32(canonical) & UNKNOWN


def phase(sample, profile):
    # This names a control schedule, never a grasp/fold success state.
    if not sample.flags & RUNNING:
        return 0
    return BLEND if sample.step >= profile['handoff_step'] or sample.flags & MANUAL_HANDOFF else ACT


def evaluate(sample, profile):
    mask = events = 0
    for i in range(14):
        low, high = profile['lower'][i]-1, profile['upper'][i]+1
        if not low <= sample.target[i] <= high:
            mask |= 1 << 0
        if not low <= sample.act[i] <= high:
            mask |= 1 << 1
        if i in ARM:
            if abs(sample.target[i]-sample.previous[i]) > profile['joint_step_limit']:
                mask |= 1 << 2
            if abs(sample.measured[i]-sample.previous[i]) > profile['tracking_limit']:
                mask |= 1 << 3
            if sample.flags & CHECK_CORRECTION:
                if abs(sample.correction[i]) > profile['correction_limit']:
                    mask |= 1 << 4
                if abs(sample.correction[i]-sample.previous_correction[i]) > profile['correction_step_limit']:
                    mask |= 1 << 5
        elif sample.target[i] != sample.act[i] or sample.correction[i] != 0:
            mask |= 1 << 6
        if sample.flags & CHECK_START:
            tolerance = profile['start_gripper_tolerance'] if i in (6, 13) else profile['start_joint_tolerance']
            if abs(sample.measured[i]-profile['start'][i]) > tolerance:
                mask |= 1 << 7
    if sample.flags & RUNNING:
        if sample.step >= profile['max_actions']:
            mask |= 1 << 8
            events |= 1 << 1
        if sample.step >= profile['handoff_step'] or sample.flags & MANUAL_HANDOFF:
            events |= 1 << 0
        if phase(sample, profile) == BLEND and sample.queue_depth == 0:
            events |= 1 << 2
    if sample.feedback_age_us == UNKNOWN:
        events |= 1 << 3
    elif profile['max_feedback_age_us'] is not None and sample.feedback_age_us > profile['max_feedback_age_us']:
        mask |= 1 << 9
    if sample.profile_crc != profile_crc(profile):
        mask |= 1 << 10
    return dict(violations=mask, events=events, phase=phase(sample, profile),
                names=[name for i, name in enumerate(NAMES) if mask & (1 << i)])


def emulate(packet, profile):
    from .protocol import RESPONSE, Sample, frame, unframe
    kind, session, sequence, tick, payload = unframe(packet)
    if kind != 1:
        raise ValueError('Expected request')
    result = evaluate(Sample.unpack(payload), profile)
    return frame(2, session, sequence, tick, RESPONSE.pack(
        profile_crc(profile), result['violations'], result['events'], result['phase'], 0))
