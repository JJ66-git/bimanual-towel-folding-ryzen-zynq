"""Detect a loaded second-fold grasp that never makes the cross-towel motion."""

from collections import deque
from dataclasses import dataclass
import math


def loaded_grip(grip, command_mm, *, effort_nm=.25, minimum_width_mm=3.):
    return (grip['width_mm'] >= minimum_width_mm and grip['width_mm']-command_mm >= .75
            and abs(grip['effort_Nm']) >= effort_nm)


@dataclass(frozen=True)
class SweepThresholds:
    initial_wait_s: float = 3.85
    minimum_progress_mm: float = 150.
    activity_window_s: float = .66
    maximum_recent_progress_mm: float = 32.
    minimum_time_left_s: float = 2.35


class SweepMonitor:
    """Consume post-regrasp feedback. Used offline by trace replay and simulation."""

    def __init__(self, now_s, across_mm, thresholds=SweepThresholds()):
        self.start_s = now_s
        self.origin_mm = across_mm
        self.thresholds = thresholds
        self.window = deque()
        self.last_s = None
        self.history_since = None
        self.peak_mm = 0.
        self.request = None
        self.released = False

    def observe(self, now_s, across_mm, *, right_loaded, left_loaded, valid,
                right_released, time_left_s):
        if self.request is not None or self.released:
            return None
        if not valid or not math.isfinite(across_mm) or (self.last_s is not None and now_s <= self.last_s):
            self.history_since = None
            self.window.clear()
            return None
        if right_released:
            self.released = True
            return None
        if self.last_s is None or now_s - self.last_s > .18:
            self.history_since = None
            self.window.clear()
        self.last_s = now_s
        progress = across_mm - self.origin_mm
        self.peak_mm = max(self.peak_mm, progress)
        if not right_loaded or left_loaded:
            self.history_since = None
            self.window.clear()
            return None
        if self.history_since is None:
            self.history_since = now_s
        t = self.thresholds
        while self.window and now_s - self.window[0][0] > t.activity_window_s:
            self.window.popleft()
        self.window.append((now_s, progress))
        if (now_s - self.start_s < t.initial_wait_s or time_left_s < t.minimum_time_left_s
                or self.peak_mm >= t.minimum_progress_mm
                or now_s - self.history_since < t.activity_window_s
                or len(self.window) < 5):
            return None
        span = max(p for _, p in self.window) - min(p for _, p in self.window)
        if span > t.maximum_recent_progress_mm:
            return None
        self.request = dict(time_s=now_s, elapsed_s=now_s-self.start_s, progress_mm=progress,
                            best_progress_mm=self.peak_mm, recent_span_mm=span)
        return self.request
