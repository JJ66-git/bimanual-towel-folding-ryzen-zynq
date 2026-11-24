#!/usr/bin/env python3
"""Audit wrist views before the first closure using saved task recordings."""

import argparse
import itertools
import json
from pathlib import Path

import cv2
import numpy as np


# The visible region between the fingers is specific to the current wrist
# mounts and 640 x 480 cameras. This is a visibility check, not a 3D measurement.
STRIPS = ((270, 330), (310, 370), (350, 410))
HSV_MIN = (82, 52, 32)
HSV_MAX = (126, 255, 245)
APPROACH_MIN_MM = 38
APPROACH_MAX_MM = 48
MAX_ALIGNMENT_S = .075
VISIBLE_FRACTION = .025


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def inspect_frame(bgr):
    if bgr.shape != (480, 640, 3):
        raise ValueError('Wrist audit expects the recorded 640 x 480 color views')
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(HSV_MIN), np.array(HSV_MAX)) > 0
    coverage = [float(mask[340:400, left:right].mean()) for left, right in STRIPS]
    upper = [float(mask[275:330, left:right].mean()) for left, right in STRIPS]
    return dict(corridor_coverage=coverage, above_corridor_coverage=upper)


def approach_rows(rows, gripper):
    for opened, group in itertools.groupby(rows, key=lambda row: row['target'][gripper] >= .015):
        if not opened:
            continue
        interval = list(group)
        if len(interval) < 20 or max(row['target'][gripper] for row in interval) <= .035:
            continue
        first_closed_step = interval[-1]['step'] + 1
        if first_closed_step >= len(rows):
            return None, []
        # Walk backward from the first load-bearing approach. This discards
        # samples when the open fingers were still on the way into view.
        selected = []
        for row in reversed(interval):
            width = row['measured'][gripper] * 1000
            if width > APPROACH_MAX_MM:
                break
            if width >= APPROACH_MIN_MM:
                selected.append(row)
        return first_closed_step, list(reversed(selected))
    return None, []


def audit_side(rows, timestamps, video, gripper):
    closes, selected = approach_rows(rows, gripper)
    records = {}
    capture = cv2.VideoCapture(str(video))
    try:
        for row in selected:
            tick = row['tick_monotonic_s']
            stamp = min(timestamps, key=lambda sample: abs(sample['monotonic_s'] - tick))
            delta = stamp['monotonic_s'] - tick
            frame_index = stamp['video_frame_index']
            if abs(delta) > MAX_ALIGNMENT_S or frame_index in records:
                continue
            if stamp['monotonic_s'] >= rows[closes]['tick_monotonic_s']:
                continue
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, bgr = capture.read()
            if not ok:
                raise ValueError(f'Cannot read frame {frame_index} from {video}')
            records[frame_index] = dict(step=row['step'], frame=frame_index, delta_ms=delta*1000,
                                        opening_mm=row['measured'][gripper]*1000, **inspect_frame(bgr))
    finally:
        capture.release()

    samples = list(records.values())
    if len(samples) < 2:
        verdict = 'insufficient_frames'
    elif all(max(sample['corridor_coverage']) < VISIBLE_FRACTION for sample in samples):
        verdict = 'cloth_not_visible_in_corridor'
    else:
        verdict = 'cloth_visible_contact_unconfirmed'
    return dict(first_close_step=closes, verdict=verdict, samples=samples)


def audit_trial(trace_path, recording):
    rows = json.loads(trace_path.read_text())['rows']
    timestamps = read_jsonl(recording / 'frame_times.jsonl')
    return {side: audit_side(rows, timestamps, recording / f'{side}.avi', gripper)
            for side, gripper in [('left', 6), ('right', 13)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial', action='append', nargs=3, metavar=('LABEL', 'TRACE', 'RECORDING'), required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    report = {}
    for label, trace, recording in args.trial:
        if label in report:
            parser.error(f'Duplicate trial label: {label}')
        report[label] = audit_trial(Path(trace), Path(recording))
        print(label, {side: row['verdict'] for side, row in report[label].items()}, flush=True)
    args.out.write_text(json.dumps(dict(trials=report, hardware_access=False,
                                        scope='Visibility in the recorded camera coordinates. Cloth contact and safe correction are unverified.'),
                                  indent=2, allow_nan=False)+'\n')


if __name__ == '__main__':
    main()
