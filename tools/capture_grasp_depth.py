#!/usr/bin/env python3
"""Capture depth from the rig without connecting to either arm."""

import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import sys

import cv2
import numpy as np


PROJECT = Path(__file__).resolve().parents[1]
SITE = PROJECT / 'amd_hybrid_deploy_20261031/piper_towel_replica/configs/cobot_site.json'
TABLE_ROI = (160, 90, 480, 190)
JAW_ROI = (270, 340, 410, 400)


def roi_statistics(depth, roi, scale):
    x0, y0, x1, y1 = roi
    values = depth[y0:y1, x0:x1]
    valid = values[values > 0]
    return dict(roi_xyxy=roi, valid_fraction=float(valid.size/values.size),
                median_m=float(np.median(valid)*scale) if valid.size else None)


def table_plane(rs, depth, intrinsics, scale):
    x0, y0, x1, y1 = TABLE_ROI
    pixels = [(x, y) for y in range(y0, y1, 5) for x in range(x0, x1, 5) if depth[y, x] != 0]
    if len(pixels) < 100:
        return dict(available=False, points=len(pixels))
    if intrinsics.model == rs.distortion.modified_brown_conrady:
        camera = np.array([[intrinsics.fx, 0, intrinsics.ppx], [0, intrinsics.fy, intrinsics.ppy], [0, 0, 1]])
        points = cv2.undistortPoints(np.asarray(pixels, dtype=np.float32).reshape(-1, 1, 2),
                                     camera, np.asarray(intrinsics.coeffs)).reshape(-1, 2)
        z = np.asarray([depth[y, x]*scale for x, y in pixels])
        xyz = np.column_stack((points*z[:, None], z))
    else:
        xyz = np.asarray([rs.rs2_deproject_pixel_to_point(intrinsics, [x, y], float(depth[y, x]*scale)) for x, y in pixels])
    rng = np.random.default_rng(29)
    inliers = np.zeros(len(xyz), dtype=bool)
    for _ in range(100):
        a, b, c = xyz[rng.choice(len(xyz), 3, replace=False)]
        normal = np.cross(b-a, c-a)
        length = np.linalg.norm(normal)
        if length < 1e-7:
            continue
        nearby = np.abs((xyz-a) @ (normal/length)) < .008
        if nearby.sum() > inliers.sum():
            inliers = nearby
    if inliers.mean() < .8:
        return dict(available=False, points=len(xyz), inlier_fraction=float(inliers.mean()))
    points = xyz[inliers]
    center = points.mean(axis=0)
    _, _, vectors = np.linalg.svd(points-center, full_matrices=False)
    normal = vectors[-1]
    if normal[2] > 0:
        normal = -normal
    distance = -float(normal @ center)
    residual_mm = np.abs(points @ normal + distance)*1000
    return dict(available=True, points=len(xyz), inlier_fraction=float(inliers.mean()),
                normal_camera=normal.tolist(), camera_to_plane_m=distance,
                median_residual_mm=float(np.median(residual_mm)), p95_residual_mm=float(np.percentile(residual_mm, 95)))


def capture_camera(rs, role, serial, output):
    pipe = rs.pipeline()
    config = rs.config()
    config.enable_device(serial)
    config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
    config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
    profile = pipe.start(config)
    try:
        align = rs.align(rs.stream.color)
        scale = profile.get_device().first_depth_sensor().get_depth_scale()
        for _ in range(35):
            frames = pipe.wait_for_frames(timeout_ms=2500)
        aligned = align.process(frames)
        depth_frame, color_frame = aligned.get_depth_frame(), aligned.get_color_frame()
        if not depth_frame or not color_frame:
            raise RuntimeError('Missing depth or color from the selected device')
        depth = np.asanyarray(depth_frame.get_data()).copy()
        bgr = np.asanyarray(color_frame.get_data()).copy()
        intrinsics = color_frame.profile.as_video_stream_profile().intrinsics
    finally:
        pipe.stop()
    if not cv2.imwrite(str(output / f'{role}_rgb.png'), bgr):
        raise OSError('Could not save RGB image')
    np.savez_compressed(output / f'{role}_depth.npz', depth_raw=depth, scale_m=scale)
    result = dict(serial=serial, depth_scale_m=scale, valid_fraction=float((depth>0).mean()),
                  color_intrinsics=dict(fx=intrinsics.fx, fy=intrinsics.fy, cx=intrinsics.ppx, cy=intrinsics.ppy,
                                        model=str(intrinsics.model), coefficients=list(intrinsics.coeffs)))
    if role == 'middle':
        result['table_roi'] = roi_statistics(depth, TABLE_ROI, scale)
        result['table_plane'] = table_plane(rs, depth, intrinsics, scale)
    else:
        result['jaw_region'] = roi_statistics(depth, JAW_ROI, scale)
    return result


def raw_depth_nodes(roles):
    nodes = {}
    expected = {serial: role for role, serial in roles.items()}
    for node in sorted(Path('/sys/class/video4linux').glob('video*'), key=lambda item: int(item.name[5:])):
        serial = next((parent.joinpath('serial').read_text().strip() for parent in node.resolve().parents
                       if parent.joinpath('serial').is_file()), None)
        role = expected.get(serial)
        if role is None or role in nodes:
            continue
        device = '/dev/' + node.name
        query = subprocess.run(['v4l2-ctl', '--device', device, '--list-formats-ext'],
                               capture_output=True, text=True, timeout=3)
        if query.returncode == 0 and "'Z16 '" in query.stdout and '640x480' in query.stdout:
            nodes[role] = device
    return nodes


def capture_raw(role, serial, device, output):
    path = output / f'{role}_depth.z16'
    subprocess.run(['v4l2-ctl', '--device', device, "--set-fmt-video=width=640,height=480,pixelformat=Z16 ",
                    '--stream-mmap=3', '--stream-skip=8', '--stream-count=1', '--stream-to='+str(path)],
                   capture_output=True, text=True, check=True, timeout=6)
    depth = np.fromfile(path, dtype='<u2')
    if depth.size != 640*480:
        raise ValueError(f'Expected one 640 x 480 Z16 frame, got {depth.size} samples')
    valid = depth[depth > 0]
    return dict(serial=serial, device=device, aligned_to_rgb=False, valid_fraction=float(valid.size/depth.size),
                depth_scale_m=None, median_raw_units=float(np.median(valid)) if valid.size else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--raw-v4l2', action='store_true', help='Use the kernel depth stream without RGB alignment')
    args = parser.parse_args()

    sys.path.insert(0, str(PROJECT / 'npu_deploy'))
    from operation_lock_path import operation_lock_path

    roles = json.loads(SITE.read_text())['camera_roles']
    results = dict(cameras={}, robot_commands_sent=False,
                   scope='Individual camera coordinates. No robot-base transform or verified fingertip calibration.')
    with operation_lock_path(PROJECT).open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        args.out.mkdir(parents=True, exist_ok=False)
        visible = set()
        sdk_by_video_path = {}
        if not args.raw_v4l2:
            try:
                import pyrealsense2 as rs
                context = rs.context()
                for device in context.query_devices():
                    serial = device.get_info(rs.camera_info.serial_number)
                    visible.add(serial)
                    if device.supports(rs.camera_info.physical_port):
                        physical_port = device.get_info(rs.camera_info.physical_port)
                        sdk_by_video_path[Path(physical_port).resolve()] = serial
                results['sdk_visible_device_count'] = len(visible)
            except (ImportError, RuntimeError) as exc:
                results['sdk_error'] = str(exc)
        nodes = raw_depth_nodes(roles) if not set(roles.values()) <= visible else {}
        for role in ('middle', 'left', 'right'):
            try:
                sdk_serial = roles[role] if roles[role] in visible else None
                if sdk_serial is None and role in nodes:
                    # Bind the USB descriptor in the site config to the exact
                    # kernel depth node. The SDK may expose a separate serial.
                    node = Path('/sys/class/video4linux') / Path(nodes[role]).name
                    sdk_serial = sdk_by_video_path.get(node.resolve())
                if sdk_serial is not None:
                    camera = capture_camera(rs, role, sdk_serial, args.out)
                    camera['configured_usb_serial'] = roles[role]
                elif role in nodes:
                    camera = capture_raw(role, roles[role], nodes[role], args.out)
                else:
                    camera = dict(error='Saved camera did not expose a supported depth stream')
                results['cameras'][role] = camera
            except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
                results['cameras'][role] = dict(error=str(exc))
            print(role, json.dumps(results['cameras'][role]), flush=True)
    (args.out/'report.json').write_text(json.dumps(results, indent=2, allow_nan=False)+'\n')
    return 1 if all('error' in camera for camera in results['cameras'].values()) else 0


if __name__ == '__main__':
    raise SystemExit(main())
