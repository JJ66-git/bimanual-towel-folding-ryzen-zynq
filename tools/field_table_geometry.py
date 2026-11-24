"""Fit the recorded table in camera coordinates using native RealSense depth."""

import argparse
import json
from pathlib import Path
import sys

import cv2
import numpy as np

from replay_first_grasp import POLICY

sys.path.insert(0, str(POLICY / "deployment_entry"))
from towel_finish import SEGMENTATION
from towel_quality import cloth_features


def camera_values(intrinsics):
    return np.array([intrinsics["fx"], intrinsics["fy"]]), np.array([intrinsics["cx"], intrinsics["cy"]])


def project(points, intrinsics):
    focal, center = camera_values(intrinsics)
    return (points[:, :2] / points[:, 2:3]) * focal + center


def intersect_plane(uv, intrinsics, normal, distance):
    focal, center = camera_values(intrinsics)
    rays = np.column_stack(((np.asarray(uv) - center) / focal, np.ones(len(uv))))
    return rays * (-distance / (rays @ normal))[:, None]


def fit_table(depth, metadata):
    y, x = np.indices(depth.shape)[:, 100:410:4, 35:605:4]
    uv = np.column_stack((x.ravel(), y.ravel()))
    depth_m = depth[y, x].ravel() * metadata["depth_scale_m"]
    depth_focal, depth_center = camera_values(metadata["depth_intrinsics"])
    points = np.column_stack(((uv - depth_center) / depth_focal, np.ones(len(uv)))) * depth_m[:, None]
    rotation = np.asarray(metadata["rotation_column_major"]).reshape(3, 3, order="F")
    color_points = points @ rotation.T + metadata["translation_m"]
    projected = project(color_points, metadata["color_intrinsics"])
    in_table = ((depth_m > .30) & (depth_m < 1.1) & (projected[:, 0] > 35)
                & (projected[:, 0] < 605) & (projected[:, 1] > 72) & (projected[:, 1] < 270))
    points = color_points[in_table]
    if len(points) < 300:
        raise ValueError("Insufficient tabletop points")
    rng = np.random.default_rng(20261120)
    best = np.zeros(len(points), dtype=bool)
    for _ in range(150):
        a, b, c = points[rng.choice(len(points), 3, replace=False)]
        normal = np.cross(b - a, c - a)
        length = np.linalg.norm(normal)
        if length < 1e-7:
            continue
        inliers = np.abs((points - a) @ (normal / length)) < .008
        if inliers.sum() > best.sum():
            best = inliers
    if best.mean() < .75:
        raise ValueError(f"Inconsistent table points: inlier fraction {best.mean():.3f}")
    center = points[best].mean(axis=0)
    _, _, axes = np.linalg.svd(points[best] - center, full_matrices=False)
    normal = axes[-1]
    if normal[2] > 0.:
        normal = -normal
    distance = -float(normal @ center)
    residual = 1000. * np.abs(points[best] @ normal + distance)
    x_axis = np.array([1., 0., 0.]) - normal[0] * normal
    x_axis /= np.linalg.norm(x_axis)
    basis = np.stack((x_axis, np.cross(normal, x_axis), normal), axis=1)
    anchor = intersect_plane([[320., 350.]], metadata["color_intrinsics"], normal, distance)[0]
    return dict(normal_camera=normal.tolist(), distance_m=distance, table_to_camera_rotation=basis.tolist(),
                table_origin_camera_m=anchor.tolist(), points=int(len(points)),
                inlier_fraction=float(best.mean()), residual_p95_mm=float(np.percentile(residual, 95)),
                color_intrinsics=metadata["color_intrinsics"])


def table_coordinates(uv, table):
    points = intersect_plane(uv, table["color_intrinsics"], np.asarray(table["normal_camera"]), table["distance_m"])
    return (points - table["table_origin_camera_m"]) @ np.asarray(table["table_to_camera_rotation"])


def towel_outline(path, table):
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError(path)
    shape = cloth_features(cv2.cvtColor(image, cv2.COLOR_BGR2RGB), SEGMENTATION)
    if shape is None:
        raise ValueError(f"No towel in {path}")
    contour = np.asarray(shape["contour"])
    xy = table_coordinates(contour, table)[:, :2]
    rectangle = cv2.minAreaRect(xy.astype(np.float32))
    corners = cv2.boxPoints(rectangle)
    return dict(size_mm=sorted([1000. * side for side in rectangle[1]], reverse=True),
                center_table_m=list(rectangle[0]), corners_table_m=corners.tolist(),
                contour_table_m=xy.round(6).tolist(),
                contour_hits_image_boundary=bool(np.any(contour.min(axis=0) <= 1)
                    or np.any(contour.max(axis=0) >= np.array((638, 478)))),
                area_px=shape["area"], camera_cloth_bbox=cv2.boundingRect(contour.astype(np.int32)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--recording", type=Path, action="append", default=[])
    args = parser.parse_args()
    raw = np.fromfile(args.raw, dtype="<u2").reshape(480, 640)
    metadata = json.loads(args.metadata.read_text())
    table = fit_table(raw, metadata)
    recordings = {str(path): dict(initial=towel_outline(path / "first_middle.png", table),
                                  final=towel_outline(path / "last_middle.png", table))
                  for path in args.recording}
    result = dict(table=table, recordings=recordings, robot_commands_sent=False,
                  scope="Metric table projection at z=0 of stationary towel outlines. "
                        "Arm mounts and lifted cloth are outside this plane fit.")
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
