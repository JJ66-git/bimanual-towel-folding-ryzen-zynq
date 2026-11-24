"""Image-space measurements for the blue towel in the middle camera."""

import argparse
import json
from pathlib import Path
import sys

import cv2
import numpy as np

DEPLOYMENT = (Path(__file__).resolve().parents[1] / 'amd_hybrid_deploy_20261031/piper_towel_replica/deployment_entry')
sys.path.insert(0, str(DEPLOYMENT))
from towel_finish import SEGMENTATION
from towel_quality import cloth_features


def fit_local_edge(columns, heights, center, *, max_slope=.52):
    """Fit the visible edge and discount the hem, small ridges, and holes."""
    x = columns - center
    if len(x) < 14 or np.ptp(x) < 20:
        return None
    design = np.column_stack((x, np.ones_like(x)))
    active = np.ones(len(x), dtype=bool)
    for _ in range(3):
        slope, intercept = np.linalg.lstsq(design[active], heights[active], rcond=None)[0]
        residual = np.abs(design @ [slope, intercept] - heights)
        active = residual <= max(2.5, 3 * np.median(residual))
        if active.sum() < 14 or np.ptp(x[active]) < 20:
            return None
    if abs(slope) > max_slope or np.percentile(residual, 90) > 5.:
        return None
    return float(intercept), float(slope), float(np.mean(active))


def far_corner(mask, side, anchor, height, slope):
    """Intersect the local far edge and the side edge above the cropped hem."""
    rows = np.arange(round(height) + 12, min(round(height) + 64, mask.shape[0]))
    if len(rows) < 22:
        return None
    occupied = mask[rows]
    left = np.argmax(occupied, axis=1)
    right = mask.shape[1] - 1 - np.argmax(occupied[:, ::-1], axis=1)
    boundary = left if side == 'left' else right
    visible = (right - left >= 72) & (boundary >= 5) & (boundary < mask.shape[1] - 5)
    fit = fit_local_edge(rows[visible], boundary[visible], height, max_slope=.8)
    if fit is None:
        return None
    x_at_height, side_slope, _ = fit
    if abs(1. - side_slope * slope) < .6:
        return None
    x = (x_at_height - side_slope * slope * anchor) / (1. - side_slope * slope)
    y = height + slope * (x - anchor)
    if not (8 <= x <= mask.shape[1] - 8 and 8 <= y <= mask.shape[0] - 8):
        return None
    return float(x), float(y)


def outer_edge(rgb):
    """Measure two supported points on the far outer edge of the region."""
    if rgb.shape != (480, 640, 3):
        raise ValueError('Expected the 640 by 480 middle-camera RGB view')
    feature = cloth_features(rgb, SEGMENTATION)
    if feature is None or feature['area'] < 18000:
        return None
    mask = feature['mask'] > 0
    columns = np.flatnonzero(mask.sum(axis=0) >= 22)
    if len(columns) < 120:
        return None
    left, right = np.percentile(columns, [2, 98])
    # A clipped edge is safe to report only at well-supported internal columns.
    span = right - left
    anchors = {'left': left + .20 * span, 'right': right - .20 * span}
    top = np.argmax(mask, axis=0)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    results = {}
    corners = {}
    for side, center in anchors.items():
        radius = float(np.clip(.075 * span, 16, 33))
        nearby = columns[np.abs(columns - center) <= radius]
        if len(nearby) < 1.5 * radius:
            results[side] = dict(reason='insufficient_cloth_support')
            continue
        fit = fit_local_edge(nearby, top[nearby], center)
        if fit is None:
            results[side] = dict(reason='irregular_or_occluded_outline')
            continue
        height, slope, support = fit
        ys = np.rint(height + (nearby - center) * slope).astype(int) + np.arange(-15, 0)[:, None]
        if ys.min() < 0:
            results[side] = dict(reason='edge_near_frame_boundary')
            continue
        if np.mean(gray[ys, nearby] < 65) > .14:
            results[side] = dict(reason='dark_object_above_edge')
            continue
        results[side] = dict(point_px=[float(center), height], slope_dy_dx=slope, support=support)
        corner = far_corner(mask, side, center, height, slope)
        if corner is not None:
            corners[side] = corner
    if len(corners) == 2 and corners['right'][0] - corners['left'][0] > .7 * span:
        # Move inward along the detected edge rather than using the full
        # bounding box, whose near hem is often clipped by the image.
        span_x = corners['right'][0] - corners['left'][0]
        for side, sign in (('left', 1), ('right', -1)):
            x, y = corners[side]
            offset = sign * .16 * span_x
            result = results[side]
            aim_x, aim_y = x + offset, y + offset * result['slope_dy_dx']
            local_x = np.arange(round(aim_x) - 9, round(aim_x) + 10)
            local_y = np.rint(aim_y + (local_x - aim_x) * result['slope_dy_dx']).astype(int)
            if np.mean(np.abs(top[local_x] - local_y) <= 4) < .8:
                continue
            above = local_y + np.arange(-10, 0)[:, None]
            if above.min() < 0 or np.mean(gray[above, local_x] < 65) > .14:
                continue
            result['corner_px'] = [x, y]
            result['point_px'] = [aim_x, aim_y]
            result['side_edge_observed'] = True
    return dict(sides=results, reference_span_px=float(span), area_px=feature['area'],
                center_px=feature['center'], rectangularity=feature['rectangularity'])


def visible_fold_seam(rgb):
    """Find a bright transition below a dark lip along the visible inner hem."""
    feature = cloth_features(rgb, SEGMENTATION)
    if feature is None:
        return None
    mask = feature['mask'] > 0
    left, _, width, _ = cv2.boundingRect(np.asarray(feature['contour'], np.int32))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    smooth = cv2.GaussianBlur(gray, (13, 5), 0)
    top = np.argmax(mask, axis=0)
    bottom = len(mask) - 1 - np.argmax(mask[::-1], axis=0)
    offsets = np.arange(12, 121)
    columns = np.arange(round(left + .18 * width), round(left + .82 * width), 5)
    xs = np.repeat(columns[:, None], len(offsets), axis=1)
    ys = top[columns, None] + offsets
    inside = (ys <= bottom[columns, None] - 10) & (bottom[columns, None] - top[columns, None] > 44)
    ys = np.clip(ys, 8, mask.shape[0] - 9)
    lip = smooth[ys - 2, xs]
    rebound = smooth[ys + 4, xs] - lip
    shadow = smooth[ys - 7, xs] - lip
    response = np.minimum(rebound, shadow * 1.65)
    response[~inside | (lip < 32)] = 0.
    # Votes at similar depth into the cloth are less sensitive to a single
    # wrinkle or jaw than the strongest gradient in one column.
    votes = []
    for nominal in offsets[5:-5]:
        near = np.abs(offsets - nominal) <= 9
        strength = response[:, near].max(axis=1)
        votes.append(float(np.count_nonzero(strength >= 6.) / len(columns)))
    strongest = int(np.argmax(votes))
    best_offset = offsets[5 + strongest]
    nearby = np.abs(offsets - best_offset) <= 9
    peaks = np.argmax(response[:, nearby], axis=1)
    strength = response[:, nearby].max(axis=1)
    valid = strength >= 6.
    if votes[strongest] < .48 or sum(valid) < 10:
        return None
    points_y = top[columns[valid]] + offsets[nearby][peaks[valid]]
    ratios = (points_y - top[columns[valid]]) / (bottom[columns[valid]] - top[columns[valid]])
    return dict(visible_support=votes[strongest], points_px=np.column_stack((columns[valid], points_y)).tolist(),
                normalized_lip_depth=float(np.median(ratios)),
                normalized_lip_depth_spread=float(np.percentile(ratios, 90) - np.percentile(ratios, 10)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True, help='Saved middle-camera RGB image')
    parser.add_argument('--table-map', type=Path, help='Optional verified map from towel_table_map.py')
    args = parser.parse_args()
    bgr = cv2.imread(str(args.image))
    if bgr is None or bgr.shape != (480, 640, 3):
        parser.error('Expected a saved 640 by 480 middle-camera image')
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    edge = outer_edge(rgb)
    points_mm = None
    if args.table_map is not None:
        from towel_table_map import project_observed_edges
        mapping = json.loads(args.table_map.read_text())
        points_mm = project_observed_edges(mapping, edge)
    print(json.dumps(dict(edge=edge, visible_seam=visible_fold_seam(rgb),
                          table_plane_targets_mm=points_mm), indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
