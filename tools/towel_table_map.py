#!/usr/bin/env python3
"""Fit and verify a planar middle-camera to table XY map from marked points."""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def points(value, name, image_size):
    result = np.asarray(value, dtype=np.float64)
    if result.ndim != 2 or result.shape[1] != 4 or not np.isfinite(result).all():
        raise ValueError(f'{name} must contain finite [u_px, v_px, x_mm, y_mm] rows')
    if np.any(result[:, :2] < 0) or np.any(result[:, :2] >= image_size):
        raise ValueError(f'{name} contains points outside the camera image')
    if len(np.unique(result[:, :2], axis=0)) != len(result):
        raise ValueError(f'{name} contains duplicate pixels')
    return result


def project(matrix, pixels):
    pixels = np.asarray(pixels, dtype=np.float64).reshape(-1, 2)
    homogeneous = np.column_stack((pixels, np.ones(len(pixels)))) @ matrix.T
    denominator = homogeneous[:, 2]
    if np.any(np.abs(denominator) < 1e-9):
        raise ValueError('Point goes to infinity in the fitted plane')
    result = homogeneous[:, :2] / denominator[:, None]
    if not np.isfinite(result).all():
        raise ValueError('Projection is not finite')
    return result


def inside_region(region, pixels):
    hull = np.asarray(region, dtype=np.float32)
    return np.asarray([cv2.pointPolygonTest(hull, tuple(map(float, pixel)), False) >= 0
                       for pixel in pixels])


def fit_table_map(data, *, max_error_mm=3.):
    image_size = np.asarray(data.get('image_size', (640, 480)))
    if image_size.shape != (2,) or image_size.tolist() != [640, 480]:
        raise ValueError('Use the 640 by 480 middle-camera images')
    fit = points(data['fit'], 'fit', image_size)
    check = points(data['check'], 'check', image_size)
    if len(fit) < 6 or len(check) < 4:
        raise ValueError('Use at least six fit points and four independent check points')
    separation = np.linalg.norm(check[:, None, :2] - fit[None, :, :2], axis=-1)
    if np.min(separation) < 3.:
        raise ValueError('Check points must be distinct from the fit points')
    if not np.isfinite(max_error_mm) or max_error_mm <= 0:
        raise ValueError('Check tolerance must be positive')
    matrix, status = cv2.findHomography(fit[:, :2], fit[:, 2:], cv2.RANSAC, max_error_mm)
    if matrix is None or status is None or int(status.sum()) < 5:
        raise ValueError('No usable plane fit; add separated table markers')
    inliers = status.ravel() > 0
    hull = cv2.convexHull(fit[inliers, :2].astype(np.float32))
    if not np.all(inside_region(hull, check[:, :2])):
        raise ValueError('Check points must be inside the calibrated tabletop region')
    check_error = np.linalg.norm(project(matrix, check[:, :2]) - check[:, 2:], axis=1)
    within_tolerance = bool(np.max(check_error) <= max_error_mm)
    return dict(schema='towel_table_xy_v1', image_size=image_size.tolist(),
                camera_id=data.get('camera_id'), input_reference=data.get('input_reference'),
                pixel_to_table_xy_mm=matrix.tolist(), calibrated_hull_px=hull[:, 0, :].tolist(),
                fit_points=len(fit), fit_inliers=int(inliers.sum()), check_points=len(check),
                check_error_mm=check_error.tolist(), check_max_error_mm=float(check_error.max()),
                max_allowed_error_mm=max_error_mm, check_passed=within_tolerance)


def project_observed_edges(mapping, observation):
    """Project supported points inside the calibrated plane, for inspection."""
    if (mapping.get('schema') != 'towel_table_xy_v1' or mapping.get('image_size') != [640, 480]
            or not mapping.get('check_passed') or observation is None):
        return None
    sides = observation['sides']
    if not all(sides[side].get('side_edge_observed') for side in ('left', 'right')):
        return None
    picks = [sides[side]['point_px'] for side in ('left', 'right')]
    hull = mapping['calibrated_hull_px']
    if not np.all(inside_region(hull, picks)):
        return None
    positions = project(np.asarray(mapping['pixel_to_table_xy_mm']), picks)
    return dict(zip(('left', 'right'), positions.tolist(), strict=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='JSON fit and check points')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--max-error-mm', type=float, default=3.)
    args = parser.parse_args()
    try:
        report = fit_table_map(json.loads(args.input.read_text()), max_error_mm=args.max_error_mm)
    except (ValueError, KeyError, cv2.error) as error:
        parser.error(str(error))
    args.out.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    return 0 if report['check_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
