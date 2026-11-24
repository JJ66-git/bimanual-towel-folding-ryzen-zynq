#!/usr/bin/env python3
"""Show the middle camera with the averaged placement of recent completed folds."""

import argparse
from contextlib import ExitStack
from datetime import datetime
import json
from pathlib import Path
import sys

import cv2
import numpy as np
from PIL import Image

from operation_lock_path import operation_lock


PROJECT = Path(__file__).resolve().parents[1]
BUNDLE = PROJECT / 'amd_hybrid_deploy_20261031'
POLICY = BUNDLE / 'piper_towel_replica'
sys.path[:0] = [str(BUNDLE), str(POLICY / 'scripts'), str(POLICY / 'deployment_entry'),
                str(POLICY / 'runtime_source' / 'src')]

from camera_inventory import query_formats, serial_ancestors
from shared_runtime.placement_camera_feed import PlacementCameraFeed
from shared_runtime.tk_support import load_tk
from towel_finish import SEGMENTATION
from towel_quality import cloth_features


IMAGE_SIZE = (640, 480)


def latest_references(logs):
    references = []
    for recording in sorted(logs.glob('*_task_recording'), reverse=True):
        console = recording / 'console.log'
        if not console.is_file():
            continue
        lines = console.read_text(encoding='utf-8', errors='replace').splitlines()
        if 'TOWEL_FINISH_HOLD' in lines and any(
            line.startswith('TOWEL_FINISH_OBSERVED=') for line in lines
        ):
            references.append(recording)
            if len(references) == 2:
                break
    return references


def reference_image(recording):
    path = recording / 'first_middle.png'
    if not path.is_file():
        raise RuntimeError(f'成功轮缺少起始画面 {path}')
    with Image.open(path) as image:
        if image.size != IMAGE_SIZE:
            raise RuntimeError(f'参考画面尺寸应为 640×480，实际为 {image.size}。')
        rgb = np.array(image.convert('RGB'))
    features = cloth_features(rgb, SEGMENTATION)
    if features is None:
        raise RuntimeError(f'参考画面中未识别到蓝色毛巾 {path}')
    return rgb, features['mask']


def average_outline(masks):
    # The zero contour of the mean signed distance averages the corresponding
    # visible edges, including where the towel extends past the image boundary.
    distances = []
    for mask in masks:
        inside = cv2.copyMakeBorder((mask > 0).astype(np.uint8), 1, 1, 1, 1,
                                    cv2.BORDER_CONSTANT, value=0)
        distances.append(cv2.distanceTransform(inside, cv2.DIST_L2, 5)
                         - cv2.distanceTransform(1 - inside, cv2.DIST_L2, 5))
    average = (np.mean(distances, axis=0)[1:-1, 1:-1] >= 0).astype(np.uint8)
    contours, _ = cv2.findContours(average, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise RuntimeError('两轮可见的毛巾位置没有足够重叠，请指定参考录像。')
    contour = max(contours, key=cv2.contourArea)
    return cv2.approxPolyDP(contour, 0.005 * cv2.arcLength(contour, True), True)


def recording_label(recording):
    stamp = '_'.join(recording.name.split('_')[:2])
    try:
        return datetime.strptime(stamp, '%Y%m%d_%H%M%S').strftime('%m-%d %H:%M:%S')
    except ValueError:
        return recording.name


def bundled_reference():
    profile = json.loads(
        (POLICY / 'deployment_assets' / 'towel_placement_reference.json').read_text()
    )
    width, height = IMAGE_SIZE
    points = np.asarray(profile['outline_px'], dtype=np.int32)
    if (profile['image_size'] != list(IMAGE_SIZE) or points.ndim != 2
            or points.shape[1] != 2 or len(points) < 3
            or np.any(points < 0) or np.any(points >= (width, height))):
        raise ValueError('默认摆位轮廓与相机尺寸不匹配。')
    background = np.full((height, width, 3), 36, dtype=np.uint8)
    return background, points.reshape((-1, 1, 2)), profile['label']


def middle_camera(site):
    serial = site['camera_roles']['middle']
    preferred = site['camera_indices']['middle']
    paths = Path('/sys/class/video4linux').glob('video[0-9]*')
    indices = []
    for path in paths:
        device = (path / 'device').resolve()
        if any(identity['value'] == serial for identity in serial_ancestors(device)):
            indices.append(int(path.name.removeprefix('video')))

    errors = []
    for index in sorted(indices, key=lambda n: (n != preferred, n)):
        node = Path(f'/dev/video{index}')
        if not node.exists():
            errors.append(f'{node} 当前不可访问')
            continue
        result = query_formats(node)
        if result['rgb_640x480']:
            return index
        if result['query_error']:
            errors.append(f"{node} {result['query_error']}")
    detail = ' / '.join(errors) or '未发现可用的 640×480 彩色视频节点'
    raise RuntimeError(f'无法打开中间相机（序列号 {serial}） · {detail}')


def draw_outline(rgb, outline):
    result = rgb.copy()
    cv2.polylines(result, [outline], True, (24, 24, 24), 5, cv2.LINE_AA)
    cv2.polylines(result, [outline], True, (255, 216, 65), 2, cv2.LINE_AA)
    return result


def show(camera, reference, outline, label, *, shared=False):
    tk = load_tk()
    try:
        window = tk.Tk()
    except tk.TclError as exc:
        raise RuntimeError(f'无法打开桌面窗口，请在比赛电脑的图形桌面运行。{exc}') from exc
    from PIL import ImageTk

    window.title('FlexiFold · 毛巾起始摆位')
    window.configure(bg='#171a1e')
    display = tk.Label(window, bg='#171a1e', borderwidth=0)
    display.pack()
    caption = tk.Label(window, fg='#f1f3f5', bg='#171a1e', pady=8, padx=16)
    caption.pack(fill='x')

    show_reference = False
    template = draw_outline(reference, outline)
    unavailable = np.zeros_like(reference)
    photo = None

    def toggle(_event):
        nonlocal show_reference
        show_reference = not show_reference

    def refresh():
        nonlocal photo
        if show_reference:
            frame, status = template, '参考照片'
        else:
            try:
                rgb = camera.read_latest()
            except (RuntimeError, OSError):
                if shared:
                    frame, status = template, '参考照片 · 等待任务相机'
                else:
                    frame, status = unavailable, '等待相机画面'
            else:
                frame = draw_outline(rgb, outline)
                status = '任务中间相机画面' if shared else '中间相机实时画面'
        photo = ImageTk.PhotoImage(Image.fromarray(frame))
        display.configure(image=photo)
        caption.configure(text=f'{status}  ·  {label}  ·  R 切换参考照片  ·  Esc 关闭')
        window.after(40, refresh)

    window.bind('<r>', toggle)
    window.bind('<R>', toggle)
    window.bind('<Escape>', lambda _event: window.quit())
    window.protocol('WM_DELETE_WINDOW', window.quit)
    try:
        refresh()
        window.mainloop()
    finally:
        window.destroy()


def main():
    parser = argparse.ArgumentParser(description='显示中间相机，叠加本机成功轮或随项目提供的摆位框。')
    parser.add_argument('--reference', type=Path, action='append',
                        help='指定参考录像目录，可重复指定并取平均')
    parser.add_argument('--snapshot', type=Path, help='导出带框的参考照片，不连接相机')
    args = parser.parse_args()

    try:
        recordings = args.reference or latest_references(POLICY / 'logs')
        if recordings:
            images, masks = zip(*(reference_image(recording) for recording in recordings))
            reference = images[0]
            outline = average_outline(masks)
            label = ' + '.join(recording_label(recording) for recording in reversed(recordings))
            if len(recordings) > 1:
                label = f'{len(recordings)} 轮平均 {label}'
            for recording in recordings:
                print(f'摆位参考 {recording / "first_middle.png"}', flush=True)
        else:
            reference, outline, label = bundled_reference()
            print(label, flush=True)
        if args.snapshot:
            Image.fromarray(draw_outline(reference, outline)).save(args.snapshot)
            return 0

        with ExitStack() as resources:
            try:
                resources.enter_context(operation_lock())
            except RuntimeError:
                print('当前流程持有设备锁，预览使用任务共享画面。', flush=True)
                show(PlacementCameraFeed(POLICY / 'logs'), reference, outline, label, shared=True)
                return 0
            site = json.loads((POLICY / 'configs' / 'cobot_site.json').read_text())
            index = middle_camera(site)
            from lerobot.cameras.opencv import OpenCVCamera, OpenCVCameraConfig

            camera = OpenCVCamera(OpenCVCameraConfig(
                index_or_path=index, width=IMAGE_SIZE[0], height=IMAGE_SIZE[1], fps=30,
            ))
            try:
                camera.connect()
                print(f'中间相机 /dev/video{index} 已开启', flush=True)
                show(camera, reference, outline, label)
            finally:
                if camera.is_connected:
                    camera.disconnect()
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f'摆位预览启动失败 · {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
