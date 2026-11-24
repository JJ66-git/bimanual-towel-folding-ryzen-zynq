"""Finish evidence for the fixed blue towel and middle camera."""

from collections import deque
import math

import cv2
import numpy as np

from towel_quality import cloth_features


SEGMENTATION = {
    'hsv_lower': [85, 55, 45],
    'hsv_upper': [135, 255, 255],
    'min_area_fraction': .025,
    'clearance_radius_fraction': .0390625,
    'dark_value': 65,
}
MAX_FRAME_AGE_S = .15
MAX_FRAME_GAP_S = .3
SAMPLE_PERIOD_S = .09
RETRY_WIDE_DURATION_S = .24
RETRY_MAX_GAP_S = .20


def usable_gripper(feedback):
    return (feedback.get('enabled') is True and feedback.get('fault_bits') == '0x0'
            and all(isinstance(feedback.get(key), (int, float)) and math.isfinite(feedback[key])
                    for key in ('width_mm', 'effort_Nm', 'sdk_receipt_timestamp'))
            and feedback['width_mm'] >= 0 and feedback['sdk_receipt_timestamp'] > 0)


class FirstFoldLandingCheck:
    """Detect a wide towel footprint after the first loaded opening."""

    def __init__(self):
        self.opened = [False, False]
        self.loaded_since = None
        self.last_loaded = None
        self.loaded_widths = None
        self.release = None
        self.samples = []
        self.incomplete = None

    def gap(self):
        if self.release is None:
            self.loaded_since = self.last_loaded = self.loaded_widths = None
        self.samples.clear()

    def update(self, evidence, previous, grips):
        if self.incomplete is not None:
            return
        now = evidence['captured_s']
        widths = [grip['width_mm'] for grip in grips]
        efforts = [abs(grip['effort_Nm']) for grip in grips]
        commands = [1000. * previous[index] for index in (6, 13)]
        if self.release is None:
            self.opened = [seen or (width >= 25. and command >= 25.)
                           for seen, width, command in zip(self.opened, widths, commands, strict=True)]
            if not all(self.opened):
                return
            if self.last_loaded is not None and now - self.last_loaded <= .65:
                opening = [side for side, width, command, effort, closed in zip(
                    ('left', 'right'), widths, commands, efforts, self.loaded_widths, strict=True)
                    if min(width, command) >= closed + .8 and effort <= .23]
                if opening and self.last_loaded - self.loaded_since >= .70:
                    self.release = dict(evidence, opening_sides=opening, width_mm=widths, effort_Nm=efforts)
            if self.release is None:
                loaded = all(width >= 1.5 and width - command >= .5 and effort >= .20
                             for width, command, effort in zip(widths, commands, efforts, strict=True))
                if loaded:
                    if self.last_loaded is None or now - self.last_loaded > .30:
                        self.loaded_since = now
                    self.last_loaded = now
                    self.loaded_widths = widths
                return
        if now - self.release['captured_s'] > .9:
            return
        if evidence['area_ratio'] < .86:
            self.samples.clear()
            return
        self.samples.append(evidence)
        if len(self.samples) >= 3 and now - self.samples[0]['captured_s'] >= .22:
            self.incomplete = dict(evidence, release=self.release, samples=len(self.samples))


class FirstFoldRetryCheck:
    """Confirm that the towel is still spread after an observed first-grasp miss."""

    def __init__(self):
        self.result = None
        self.gap()

    def gap(self):
        self.since = self.last = None
        self.samples = 0

    def update(self, evidence):
        now = evidence['captured_s']
        if not (.86 <= evidence['area_ratio'] <= 1.12 and .87 <= evidence['length_ratio'] <= 1.14):
            self.gap()
            return
        if self.last is None or not 0 < now - self.last <= RETRY_MAX_GAP_S:
            self.gap()
            self.since = now
        self.last = now
        self.samples += 1
        if self.samples >= 3 and now - self.since >= RETRY_WIDE_DURATION_S:
            self.result = dict(evidence, samples=self.samples, wide_s=now - self.since)


class TowelFinishCheck:
    """Require the first fold, a loaded regrasp, and a compact cloth after retreat."""

    def __init__(self, start_rgb):
        shape = self.geometry(start_rgb)
        self.initial = shape if shape and shape['area'] >= 25000 else None
        self.first_fold = None
        self.regrasp = None
        self.finish = None
        self.right_opened = False
        self.right_loaded_since = None
        self.first_fold_since = None
        self.window = deque(maxlen=12)
        self.last_frame = -1
        self.last_sample = None
        self.last_receipts = (0., 0.)
        self.landing = FirstFoldLandingCheck()
        self.retry = FirstFoldRetryCheck()

    @staticmethod
    def geometry(rgb):
        if rgb.shape != (480, 640, 3):
            return None
        feature = cloth_features(rgb, SEGMENTATION)
        if feature is None:
            return None
        contour = np.asarray(feature['contour'], np.int32)
        length = max(cv2.minAreaRect(contour)[1])
        return dict(area=feature['area'], length=length, center=feature['center'],
                    rectangularity=feature['rectangularity'], solidity=feature['solidity'],
                    dark_fraction=feature['dark_fraction'], bbox=cv2.boundingRect(contour))

    @staticmethod
    def retreated(measured, *, holding=False):
        j2_limit, j3_limit = (1.43, -.96) if holding else (1.35, -.92)
        return (np.isfinite(measured).all()
                and all(-.05 <= measured[i] <= j2_limit for i in (1, 8))
                and all(j3_limit <= measured[i] <= .06 for i in (2, 9)))

    def reset_visual_continuity(self):
        self.window.clear()
        self.first_fold_since = self.right_loaded_since = None
        self.landing.gap()
        self.retry.gap()

    def update(self, rgb, *, frame_id, captured_s, now_s, step, measured, previous, grippers,
               held_since=None, first_fold_retry=False):
        if self.initial is None or frame_id <= self.last_frame:
            return None
        holding = held_since is not None
        if holding:
            if self.regrasp is None or captured_s <= held_since:
                return None
            if self.window and self.window[0][0] <= held_since:
                self.window.clear()
        self.last_frame = frame_id
        age = now_s - captured_s
        gap = None if self.last_sample is None else captured_s - self.last_sample
        if not 0 <= age <= MAX_FRAME_AGE_S or (gap is not None and gap <= 0):
            self.reset_visual_continuity()
            return None
        if gap is not None and gap < SAMPLE_PERIOD_S:
            return None
        if gap is not None and gap > MAX_FRAME_GAP_S:
            self.reset_visual_continuity()
        self.last_sample = captured_s
        left, right = (grippers.get(side, {}) for side in ('left', 'right'))
        if not all(map(usable_gripper, (left, right))):
            self.reset_visual_continuity()
            return None
        receipts = (left['sdk_receipt_timestamp'], right['sdk_receipt_timestamp'])
        if any(stamp <= last for stamp, last in zip(receipts, self.last_receipts)):
            self.reset_visual_continuity()
            return None
        self.last_receipts = receipts
        shape = self.geometry(rgb)
        if shape is None:
            self.reset_visual_continuity()
            return None
        area_ratio = shape['area'] / self.initial['area']
        length_ratio = shape['length'] / self.initial['length']
        evidence = dict(step=step, frame_id=frame_id, area_ratio=area_ratio,
                        length_ratio=length_ratio, captured_s=captured_s)

        if self.first_fold is None:
            self.landing.update(evidence, previous, (left, right))
            if (.50 <= area_ratio <= .78 and .83 <= length_ratio <= 1.12
                    and shape['rectangularity'] >= .80):
                if self.first_fold_since is None:
                    self.first_fold_since = captured_s
                if captured_s - self.first_fold_since >= .22:
                    self.first_fold = evidence
            else:
                self.first_fold_since = None
            if first_fold_retry and not holding and self.first_fold is None:
                self.retry.update(evidence)
            else:
                self.retry.gap()
            return None

        if self.regrasp is None:
            width = right['width_mm']
            effort = abs(right['effort_Nm'])
            if previous[13] >= .025 and width >= 25.:
                self.right_opened = True
            loaded = (self.right_opened and previous[13] < .012 and width >= 3.
                      and width - previous[13]*1000 >= .8 and effort >= .25)
            if loaded:
                if self.right_loaded_since is None:
                    self.right_loaded_since = captured_s
                if captured_s - self.right_loaded_since >= .16:
                    self.regrasp = dict(evidence, width_mm=width, effort_Nm=effort)
            else:
                self.right_loaded_since = None
            return None

        x, y, width, _ = shape['bbox']
        # The field camera can clip the near edge. The lateral edges and the
        # measured arm retreat still need to agree with the compact shape.
        clear_outline = (x >= 8 and y >= 8 and x+width <= 632
                         and shape['rectangularity'] >= .76 and shape['solidity'] >= .89
                         and shape['dark_fraction'] <= .40)
        if holding:
            clear_outline = (clear_outline and shape['rectangularity'] >= .82
                             and shape['solidity'] >= .92 and shape['dark_fraction'] <= .10)
        released = all(grip['width_mm'] <= 2.5 and abs(grip['effort_Nm']) <= .18
                       for grip in (left, right))
        if not (.26 <= area_ratio <= .48 and .40 <= length_ratio <= .72
                and clear_outline and released and self.retreated(measured, holding=holding)):
            self.window.clear()
            return None
        if self.window:
            _, first_center, first_area = self.window[0]
            if (math.dist(shape['center'], first_center) > 6
                    or abs(shape['area']/first_area - 1.) > .05):
                self.window.clear()
        self.window.append((captured_s, shape['center'], shape['area']))
        if len(self.window) >= 5 and captured_s - self.window[0][0] >= .36:
            self.finish = dict(evidence, samples=len(self.window))
            if holding:
                self.finish['confirmed_while_held'] = True
            return self.finish
        return None

    def report(self):
        return dict(first_fold=self.first_fold, first_fold_landing_incomplete=self.landing.incomplete,
                    first_fold_retry=self.retry.result, regrasp=self.regrasp, finish=self.finish)
