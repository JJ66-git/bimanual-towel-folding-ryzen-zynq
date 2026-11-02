"""Optional RGB-only staging check and stop request, shared with simulation.

This is a visual heuristic for the fixed blue-towel scene, not a fold/layer
success certificate. It never modifies policy images, actions or limits.
"""
import json
import math
from pathlib import Path

import cv2
import numpy as np


def cloth_features(rgb, settings):
    if not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError('Quality monitor requires uint8 RGB')
    height, width = rgb.shape[:2]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, np.array(settings['hsv_lower']), np.array(settings['hsv_upper']))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    area = float(cv2.contourArea(contour))
    if area < settings['min_area_fraction'] * width * height:
        return None
    hull = cv2.convexHull(contour)
    rectangle = cv2.minAreaRect(contour)
    rectangle_area = float(np.prod(rectangle[1]))
    hull_area = float(cv2.contourArea(hull))
    filled = np.zeros(mask.shape, np.uint8)
    cv2.drawContours(filled, [contour], -1, 255, -1)
    region = np.zeros(mask.shape, np.uint8)
    cv2.fillConvexPoly(region, hull, 255)
    # Rim scales with image size. Fixed camera geometry is part of the profile.
    radius = max(1, round(settings['clearance_radius_fraction'] * width))
    region = cv2.dilate(region, np.ones((2 * radius + 1, 2 * radius + 1), np.uint8))
    dark = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY) < settings['dark_value']
    dark_fraction = float(np.count_nonzero(dark & (region > 0)) / np.count_nonzero(region))
    return dict(area=area, center=list(map(float, rectangle[0])),
                rectangularity=area / max(rectangle_area, 1.), solidity=area / max(hull_area, 1.),
                dark_fraction=dark_fraction, contour=contour[:, 0, :].tolist(), mask=filled)


class TowelQualityMonitor:
    def __init__(self, profile):
        self.profile_path = Path(profile).resolve()
        self.profile = json.loads(self.profile_path.read_text())
        if self.profile.get('schema') != 'blue_towel_quality_v1':
            raise ValueError('Unsupported towel quality profile')
        if type(self.profile.get('start_check_enforced', True)) is not bool:
            raise ValueError('start_check_enforced must be a boolean')
        self.baseline_area = None
        self.first_fold_seen = False
        self.window = []
        self.last_sample = None
        self.stop_request = False
        self.rows = []
        self.start_report = None

    def check_start(self, rgb, *, enforce=None):
        p = self.profile
        profile_mode = enforce is None
        if enforce is None:
            enforce = p.get('start_check_enforced', True)
        if list(rgb.shape[:2]) != p['image_shape_hw']:
            raise ValueError('Quality profile camera resolution changed')
        feature = cloth_features(rgb, p['segmentation'])
        if feature is None:
            raise ValueError('No sufficiently visible blue towel in start image')
        reference = np.zeros(rgb.shape[:2], np.uint8)
        cv2.fillPoly(reference, [np.asarray(p['start_reference_contour'], np.int32)], 255)
        current = feature['mask'] > 0
        expected = reference > 0
        iou = float(np.count_nonzero(current & expected) / max(np.count_nonzero(current | expected), 1))
        self.baseline_area = feature['area']
        self.start_report = dict(iou=iou, required_iou=p['start_min_iou'],
                                 accepted=iou >= p['start_min_iou'], enforced=enforce,
                                 area=self.baseline_area, center=feature['center'])
        if enforce and not self.start_report['accepted']:
            raise RuntimeError(f"Start towel placement differs from reference: IoU={iou:.3f} < {p['start_min_iou']:.3f}; reposition towel before execution")
        if profile_mode and not enforce and not self.start_report['accepted']:
            print(f"TOWEL_START_ADVISORY: IoU={iou:.3f} < reference {p['start_min_iou']:.3f}; placement difference recorded, continuing original policy", flush=True)
        return self.start_report

    def update(self, rgb, elapsed_s):
        p = self.profile
        if self.baseline_area is None:
            raise RuntimeError('Quality monitor needs a start image')
        if p.get('finish_stop_enabled', True) is False:
            return False  # Staging-only candidate adds no per-tick vision work.
        if not math.isfinite(elapsed_s) or list(rgb.shape[:2]) != p['image_shape_hw']:
            raise ValueError('Quality observation time/resolution is invalid')
        if self.last_sample is not None and elapsed_s - self.last_sample < p['sample_period_s'] - 1e-6:
            return self.stop_request
        # Monotonic time and consecutive observations are required; never count
        # a repeated/stale timestamp or a gap as stable evidence.
        if self.last_sample is not None and elapsed_s - self.last_sample > p['max_sample_gap_s']:
            self.window.clear()
        self.last_sample = elapsed_s
        feature = cloth_features(rgb, p['segmentation'])
        row = dict(elapsed_s=float(elapsed_s), visible=feature is not None)
        eligible = False
        if feature is not None:
            ratio = feature['area'] / self.baseline_area
            row.update({k: v for k, v in feature.items() if k not in ('mask', 'contour')})
            row['area_ratio'] = ratio
            fold = p['first_fold']
            if fold['min_area_ratio'] <= ratio <= fold['max_area_ratio'] and feature['rectangularity'] >= fold['min_rectangularity']:
                self.first_fold_seen = True
            end = p['finish']
            eligible = (p.get('finish_stop_enabled', True) and elapsed_s >= end['min_elapsed_s'] and self.first_fold_seen
                        and end['min_area_ratio'] <= ratio <= end['max_area_ratio']
                        and feature['rectangularity'] >= end['min_rectangularity']
                        and feature['solidity'] >= end['min_solidity']
                        and feature['dark_fraction'] <= end['max_dark_fraction'])
        if not eligible:
            self.window.clear()
        else:
            self.window.append(row)
            end = p['finish']
            centers = np.array([r['center'] for r in self.window])
            areas = np.array([r['area'] for r in self.window])
            stable = (float(np.linalg.norm(centers - centers[0], axis=1).max()) <= end['max_center_drift_px']
                      and float(np.max(np.abs(areas / areas[0] - 1))) <= end['max_area_drift_fraction'])
            if not stable:
                self.window = [row]
            if (len(self.window) >= end['min_samples']
                    and elapsed_s - self.window[0]['elapsed_s'] >= end['stable_s']):
                self.stop_request = True
        row.update(first_fold_seen=self.first_fold_seen, eligible=eligible, stop_request=self.stop_request)
        self.rows.append(row)
        return self.stop_request

    def report(self):
        return dict(profile=str(self.profile_path), start=self.start_report,
                    finish_stop_enabled=self.profile.get('finish_stop_enabled', True),
                    stop_requested=self.stop_request, rows=self.rows,
                    task_success_certified=False,
                    limitation='RGB compactness/clearance only; hidden layers and contact are unobserved')

    def preview_guide(self, images):
        """Preview copies only; recordings and policy receive original RGB."""
        rgb=images['middle']
        if list(rgb.shape[:2]) == self.profile['image_shape_hw']:
            contour=np.asarray(self.profile['start_reference_contour'],np.int32)
            cv2.polylines(rgb,[contour],True,(60,255,90),2)
            cv2.putText(rgb,'GREEN: SUCCESSFUL START OUTLINE',(12,25),
                        cv2.FONT_HERSHEY_SIMPLEX,.55,(60,255,90),2)
        return images
