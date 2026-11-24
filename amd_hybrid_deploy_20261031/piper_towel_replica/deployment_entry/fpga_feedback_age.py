"""Convert SDK wall-clock feedback timestamps to a guarded monotonic age."""
import math
import time

UNKNOWN = 0xFFFFFFFF
MAX_VALUE = 0xFFFFFFFE


class FeedbackAgeClock:
    def __init__(self, *, wall_time=time.time, monotonic_ns=time.monotonic_ns,
                 max_offset_step_s=0.050, plausible_skew_s=60.0, allow_wall_clock=False):
        self.wall_time = wall_time
        self.monotonic_ns = monotonic_ns
        self.max_offset_step_s = max_offset_step_s
        self.plausible_skew_s = plausible_skew_s
        self.offset_s = None
        self.allow_wall_clock = allow_wall_clock

    def age_us(self, snapshot):
        if isinstance(snapshot,dict) and all(isinstance(snapshot.get(side),dict) and
                snapshot[side].get('source')=='sdk_monotonic_atomic' for side in ('left','right')):
            values=[snapshot[side].get(part+'_monotonic_ns') for side in ('left','right') for part in ('joint','gripper')]
            now=self.monotonic_ns()
            if any(type(value) is not int or value<=0 or value>now for value in values):return UNKNOWN
            return min(MAX_VALUE,(now-min(values)+999)//1000)
        if not self.allow_wall_clock:return UNKNOWN
        wall_now = self.wall_time()
        mono_now = self.monotonic_ns() / 1_000_000_000
        offset = mono_now - wall_now
        if self.offset_s is not None and abs(offset - self.offset_s) > self.max_offset_step_s:
            self.offset_s = offset
            return UNKNOWN
        self.offset_s = offset
        if not isinstance(snapshot, dict):
            return UNKNOWN
        timestamps = []
        for side in ("left", "right"):
            arm = snapshot.get(side)
            if not isinstance(arm, dict):
                return UNKNOWN
            for part in ("joint", "gripper"):
                value = arm.get(part)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return UNKNOWN
                stamp = float(value)
                if not math.isfinite(stamp) or stamp <= 0 or abs(stamp - wall_now) > self.plausible_skew_s:
                    return UNKNOWN
                timestamps.append(stamp)
        oldest_age = max(wall_now - stamp for stamp in timestamps)
        if oldest_age < 0:
            return UNKNOWN
        return min(MAX_VALUE, math.ceil(oldest_age * 1_000_000))
