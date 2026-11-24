"""Recognize a sustained unloaded closure during the first towel grasp."""

from dataclasses import dataclass
import math

import cv2
import numpy as np


OPEN_M = .025
CLOSE_M = .006
MAX_CLOSED_TARGET_M = .009
EMPTY_WIDTH_MM = 3.0
EMPTY_EFFORT_NM = .23
LOADED_WIDTH_MM = 3.0
LOADED_EFFORT_NM = .30
LOADED_TARGET_GAP_MM = 1.2
LOAD_CONFIRM_S = .18
SETTLE_S = .4
CONFIRM_S = .23
WINDOW_S = 1.4
MAX_SAMPLE_GAP_S = .15


def broad_wrist_cloth(rgb):
    """Detect a broad blue panel well above the pinch zone in current wrist views."""
    if rgb.shape != (480, 640, 3):
        return False
    hsv = cv2.cvtColor(rgb[190:256, 265:375], cv2.COLOR_RGB2HSV)
    blue = cv2.inRange(hsv, (82, 52, 32), (126, 255, 245)) > 0
    return all(np.count_nonzero(blue[:, start:start+30].mean(axis=1) >= .55) >= 5
               for start in (0, 40, 80))


class WristCornerCheck:
    """Record sustained close-range fabric coverage before the first pinch."""

    def __init__(self):
        self.frame_ids = {'left': -1, 'right': -1}
        self.candidates = {}
        self.evidence = {}

    def update(self, side, rgb, frame_id, captured_s, now, step, target_m, feedback):
        width = feedback.get('width_mm')
        if (side in self.evidence or width is None or not 35. <= width <= 52.
                or not .015 <= target_m < .048 or width - target_m * 1000 < 3.
                or feedback.get('enabled') is not True or feedback.get('fault_bits') != '0x0'
                or not 0 <= now - captured_s <= .12):
            self.candidates.pop(side, None)
            return None
        if frame_id <= self.frame_ids[side]:
            return None
        self.frame_ids[side] = frame_id
        if not broad_wrist_cloth(rgb):
            self.candidates.pop(side, None)
            return None
        first, last = self.candidates.get(side, (captured_s, captured_s))
        if captured_s - last > .16:
            first = captured_s
        self.candidates[side] = (first, captured_s)
        if captured_s - first < .075:
            return None
        result = dict(step=step, frame_id=frame_id, width_mm=width, duration_s=captured_s-first)
        self.evidence[side] = result
        return result


@dataclass
class GripperCheck:
    opened: bool = False
    closed_at: float | None = None
    done: bool = False
    loaded_since: float | None = None
    empty_since: float | None = None
    sample_time: float | None = None
    receipt: float = 0.
    load_observed: dict | None = None
    load_drop: dict | None = None

    def update(self, now, target_m, feedback):
        if self.done:
            return None
        if self.closed_at is not None and (now-self.closed_at > WINDOW_S or target_m >= OPEN_M):
            self.done = True
            return None

        if self.opened and self.closed_at is None and target_m < CLOSE_M:
            self.closed_at = now

        width = feedback.get('width_mm')
        effort = feedback.get('effort_Nm')
        receipt = feedback.get('sdk_receipt_timestamp')
        valid = (feedback.get('enabled') is True and feedback.get('fault_bits') == '0x0'
                 and all(isinstance(value, (int, float)) and math.isfinite(value)
                         for value in (width, effort, receipt))
                 and width >= 0 and receipt > self.receipt)
        if not valid:
            self.loaded_since = None
            self.empty_since = None
            self.sample_time = None
            return None

        self.receipt = receipt
        if target_m >= OPEN_M and width >= OPEN_M * 1000:
            self.opened = True
        sample_gap = self.sample_time is None or now-self.sample_time > MAX_SAMPLE_GAP_S
        self.sample_time = now
        if self.closed_at is None or now-self.closed_at < SETTLE_S or target_m >= MAX_CLOSED_TARGET_M:
            self.loaded_since = None
            self.empty_since = None
            return None
        loaded = (width >= LOADED_WIDTH_MM and abs(effort) >= LOADED_EFFORT_NM
                  and width-1000*target_m >= LOADED_TARGET_GAP_MM)
        if loaded and self.load_observed is None:
            if self.loaded_since is None or sample_gap:
                self.loaded_since = now
            if now-self.loaded_since >= LOAD_CONFIRM_S:
                self.load_observed = dict(width_mm=width, effort_Nm=effort, closed_s=now-self.closed_at,
                                          loaded_s=now-self.loaded_since)
        else:
            self.loaded_since = None

        if width > EMPTY_WIDTH_MM or abs(effort) > EMPTY_EFFORT_NM:
            self.empty_since = None
            return None
        if self.empty_since is None or sample_gap:
            self.empty_since = now
        if now-self.empty_since >= CONFIRM_S:
            self.done = True
            unloaded = dict(width_mm=width, effort_Nm=effort, sdk_receipt_timestamp=receipt,
                            closed_s=now-self.closed_at, unloaded_s=now-self.empty_since)
            if self.load_observed is not None:
                self.load_drop = dict(load_observed=self.load_observed, later_unloaded=unloaded)
                return None
            return unloaded
        return None


class FirstGraspCheck:
    """Evaluate saved or live feedback without generating a replacement target."""

    def __init__(self):
        self.left = GripperCheck()
        self.right = GripperCheck()

    @property
    def done(self):
        return self.left.done and self.right.done

    @property
    def load_drops(self):
        return {side: checker.load_drop for side, checker in [('left', self.left), ('right', self.right)]
                if checker.load_drop is not None}

    def update(self, now, previous_target, feedback):
        misses = {}
        for side, index in (('left', 6), ('right', 13)):
            evidence = getattr(self, side).update(now, previous_target[index], feedback.get(side, {}))
            if evidence is not None:
                misses[side] = evidence
        return misses
