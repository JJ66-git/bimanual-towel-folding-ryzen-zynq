#!/usr/bin/env python3
"""Compare saved controller/video records. Never opens a camera or robot.

Step 300 is a controller handoff, not a visual phase label. Pose DTW alignment
is exploratory; replayed alternative openings are not closed-loop outcomes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ARM = [0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12]


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def opening_episodes(rows, threshold_mm=25):
    episodes = []
    for row in rows:
        value = row['target'][13] * 1000
        if value >= threshold_mm:
            if not episodes or episodes[-1]['end_step'] != row['step'] - 1:
                episodes.append(dict(start_step=row['step'], end_step=row['step'], peak_mm=value))
            episodes[-1].update(end_step=row['step'], peak_mm=max(episodes[-1]['peak_mm'], value))
    return episodes


def pose_alignment(reference, current):
    """Monotone alignment by measured joints only, excluding both grippers."""
    a = np.asarray([row['measured'] for row in current])[:, ARM]
    b = np.asarray([row['measured'] for row in reference])[:, ARM]
    n, m = len(a), len(b)
    local = np.sqrt(np.mean((a[:, None, :] - b[None, :, :]) ** 2, axis=2))
    cost = np.full((n + 1, m + 1), np.inf)
    cost[0, 0] = 0
    for i in range(1, n + 1):
        for j in range(max(1, int((i / n - .25) * m)), min(m, int((i / n + .25) * m)) + 1):
            cost[i, j] = local[i-1, j-1] + min(cost[i-1, j-1], cost[i-1, j], cost[i, j-1])
    if not np.isfinite(cost[n, m]):
        raise ValueError('No constrained alignment path')
    pairs, i, j = [], n, m
    while i and j:
        pairs.append((i-1, j-1))
        choice = int(np.argmin((cost[i-1, j-1], cost[i-1, j], cost[i, j-1])))
        i, j = ((i-1, j-1), (i-1, j), (i, j-1))[choice]
    pairs.reverse()
    mapping = {}
    for i, j in pairs:
        mapping.setdefault(i, []).append(j)
    rows = []
    for i, indices in mapping.items():
        j = int(np.median(indices))
        rows.append(dict(current_step=current[i]['step'], reference_step=reference[j]['step'],
                         measured_joint_rms_rad=float(local[i, j]),
                         current_right_mm=current[i]['target'][13] * 1000,
                         reference_right_mm=reference[j]['target'][13] * 1000))
    return rows


def contact_sheet(run, rows, settings, directory, cloth_features, side='middle'):
    recording = Path(run['recording'])
    video, times = recording / (side+'.avi'), recording / 'frame_times.jsonl'
    if not video.is_file() or not times.is_file():
        return dict(available=False, reason='Original robot-camera video is missing; trace comparison only.')
    stamps = [json.loads(line) for line in times.read_text().splitlines()]
    stamps.sort(key=lambda row: row['monotonic_s'])
    clock = np.asarray([row['monotonic_s'] for row in stamps])
    steps = [0, 150, 250, 300, 350, 400, 450, 500, 550, 600, 650, len(rows)-1]
    steps = list(dict.fromkeys(min(step, len(rows)-1) for step in steps))
    sheet = Image.new('RGB', (1280, 48 + 300 * ((len(steps)+3)//4)), '#f3f1e9')
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', 14, index=2)
    draw.text((12, 10), run['label'] + ' / ' + run['outcome'] + ' / '+side, font=font, fill='#1b2822')
    cap = cv2.VideoCapture(str(video));samples = []
    try:
        for cell, step in enumerate(steps):
            row = rows[step]
            tick = row.get('dispatch_done_monotonic_s') or row['tick_monotonic_s']
            k = int(np.argmin(np.abs(clock - tick)));stamp = stamps[k]
            cap.set(cv2.CAP_PROP_POS_FRAMES, stamp['video_frame_index'])
            ok, bgr = cap.read()
            if not ok:
                raise ValueError('Cannot read saved video frame ' + str(stamp['video_frame_index']))
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            feature = cloth_features(rgb, settings)
            sample = dict(step=step, video_frame=stamp['video_frame_index'],
                          camera_minus_dispatch_ms=(stamp['monotonic_s'] - tick)*1000,
                          right_command_mm=row['target'][13]*1000, right_feedback_mm=row['measured'][13]*1000)
            if feature:
                contour = np.asarray(feature['contour'], dtype=np.int32)
                x, y, w, h = cv2.boundingRect(contour)
                cv2.polylines(rgb, [contour], True, (255, 225, 70), 2)
                sample.update(cloth_bbox_xywh=[x, y, w, h], visible_area=feature['area'],
                              touches_right_image_border=x+w >= rgb.shape[1]-1,
                              touches_bottom_image_border=y+h >= rgb.shape[0]-1)
            samples.append(sample)
            x, y = (cell % 4) * 320, 48 + (cell // 4) * 300
            sheet.paste(Image.fromarray(rgb).resize((320, 240)), (x, y))
            draw.text((x+5, y+243), f"step {step} | R target {sample['right_command_mm']:.1f} / feedback {sample['right_feedback_mm']:.1f} mm", font=font, fill='#1b2822')
            if sample.get('touches_right_image_border'):
                draw.text((x+5, y+265), '图像右侧轮廓被裁切，不能视为完整边界', font=font, fill='#9f382f')
    finally:
        cap.release()
    name = run['label'] + '_'+side+'_contact_sheet.png'
    sheet.save(directory / name)
    return dict(available=True, video=str(video), video_sha256=digest(video), samples=samples, sheet=name,
                scope='Yellow outlines are recorded-image segmentation only; image-right is not calibrated robot-right.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--plot-library', type=Path)
    args = parser.parse_args()
    if args.plot_library:
        sys.path.append(str(args.plot_library))  # Read an existing analysis dependency, never install into policy runtime.
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root/'amd_hybrid_deploy_20261031/piper_towel_replica/deployment_entry'))
    from towel_quality import cloth_features
    manifest = json.loads(args.manifest.read_text())
    settings = json.loads(Path(manifest['segmentation_profile']).read_text())['segmentation']
    args.out.mkdir(parents=True, exist_ok=False)
    figure, axes = plt.subplots(3, 2, figsize=(16, 12), constrained_layout=True)
    loaded, reports = {}, []
    for axis, run in zip(axes.flat, manifest['runs']):
        trace = json.loads(Path(run['trace']).read_text());rows = trace['rows'];loaded[run['label']] = rows
        steps = [row['step'] for row in rows]
        target = np.asarray([row['target'][13]*1000 for row in rows])
        feedback = np.asarray([row['measured'][13]*1000 for row in rows])
        candidate = np.asarray([row['smol_candidate'][13]*1000 if row.get('smol_candidate') is not None else np.nan for row in rows])
        axis.plot(steps, target, label='ACT / dispatched target', color='#28623d')
        axis.plot(steps, feedback, label='SDK cached feedback', color='#777777', alpha=.7)
        axis.plot(steps, candidate, label='SmolVLA candidate (unused gripper)', color='#3276a8', alpha=.7)
        axis.axvline(300, linestyle=':', color='#bc6430', label='model handoff (not visual phase)')
        axis.set(title=run['label']+' / '+run['outcome'], xlabel='controller step', ylabel='right opening (mm)', ylim=(-2, 85))
        axis.grid(alpha=.2);axis.legend(fontsize=8)
        episodes = opening_episodes(rows)
        after = [row for row in rows if row['step'] >= 300]
        report = dict(run, trace_sha256=digest(run['trace']), steps=len(rows),
                      right_open_episodes_ge_25mm=episodes,
                      post_handoff_right_target_max_mm=max(row['target'][13]*1000 for row in after),
                      post_handoff_right_feedback_max_mm=max(row['measured'][13]*1000 for row in after),
                      video=contact_sheet(run, rows, settings, args.out, cloth_features),
                      right_wrist_video=contact_sheet(run, rows, settings, args.out, cloth_features, side='right'))
        reports.append(report)
    figure.savefig(args.out/'right_gripper_comparison.png', dpi=150);plt.close(figure)
    alignment = pose_alignment(loaded['full_trial_2'], loaded['buffered_full_trial'])
    (args.out/'pose_alignment.json').write_text(json.dumps(alignment, indent=2)+'\n')
    x = [row['current_step'] for row in alignment]
    figure, axis = plt.subplots(figsize=(12, 4), constrained_layout=True)
    axis.plot(x, [row['current_right_mm'] for row in alignment], label='latest ACT opening', color='#28623d')
    axis.plot(x, [row['reference_right_mm'] for row in alignment], label='successful local run, pose-aligned', color='#3276a8')
    axis.set(xlabel='latest controller step', ylabel='right opening (mm)', title='Exploratory pose alignment; no visual-phase or counterfactual success claim')
    axis.legend();axis.grid(alpha=.2);figure.savefig(args.out/'pose_aligned_opening.png', dpi=150);plt.close(figure)
    result = dict(schema='offline_right_regrasp_analysis_v1', runs=reports,
                  alignment_reference='full_trial_2', alignment_current='buffered_full_trial',
                  alignment_method='Constrained DTW on 12 measured joints; both grippers excluded; normalized progress band 0.25.',
                  alignment_mean_joint_rms_rad=float(np.mean([row['measured_joint_rms_rad'] for row in alignment])),
                  robot_commands_sent=False, live_cameras_opened=False, policy_changed=False,
                  limitations=['A controller step/model handoff is not a certified folding phase.',
                               'Cached gripper feedback is not a synchronized per-command actuator measurement.',
                               'Image contour extents are not calibrated robot-space grasp coordinates.',
                               'Missing reference videos stay missing; alternative openings do not prove a successful physical trajectory.'])
    (args.out/'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(dict(out=str(args.out), runs=len(reports), robot_commands_sent=False, policy_changed=False)))


if __name__ == '__main__':
    main()
