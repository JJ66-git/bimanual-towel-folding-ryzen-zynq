#!/usr/bin/env python3
"""Replay the normal towel finish decision on paired saved RGB and feedback."""

import argparse
from bisect import bisect_left
import json
from pathlib import Path
import sys
import time

import cv2
import numpy as np

from replay_first_grasp import POLICY, read_json, read_jsonl

sys.path.insert(0, str(POLICY / 'deployment_entry'))
from towel_finish import TowelFinishCheck


def replay(recording, rows):
    bgr = cv2.imread(str(recording / 'first_middle.png'))
    if bgr is None:
        raise ValueError(f'Missing first middle frame in {recording}')
    check = TowelFinishCheck(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    ticks = [row['tick_monotonic_s'] for row in rows]
    timings = []
    camera = cv2.VideoCapture(str(recording / 'middle.avi'))
    try:
        for stamp in read_jsonl(recording / 'frame_times.jsonl'):
            frame_id = stamp['video_frame_index']
            captured = stamp['monotonic_s']
            index = bisect_left(ticks, captured)
            if index == len(rows):
                break
            row = rows[index]
            camera.set(cv2.CAP_PROP_POS_FRAMES, frame_id)
            ok, bgr = camera.read()
            if not ok:
                raise ValueError(f'Missing frame {frame_id} in {recording}')
            start = time.perf_counter()
            completed = check.update(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB),
                                     frame_id=frame_id, captured_s=captured, now_s=ticks[index],
                                     step=row['step'], measured=row['measured'], previous=row['previous'],
                                     grippers=row.get('gripper_feedback_before_action') or {})
            timings.append((time.perf_counter() - start) * 1000)
            if completed:
                break
    finally:
        camera.release()
    report = check.report()
    finish = report['finish']
    return dict(**report, recorded_actions=len(rows),
                unchanged_tail_actions=None if finish is None else len(rows)-finish['step'],
                sample_p95_ms=float(np.percentile(timings, 95)) if timings else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    results = []
    for run in read_json(args.manifest)['runs']:
        rows = read_json(Path(run['trace']))['rows']
        result = replay(Path(run['recording']), rows)
        results.append(dict(label=run['label'], observed_outcome=run['outcome'], **result))
        print(run['label'], result['first_fold'] and result['first_fold']['step'],
              result['regrasp'] and result['regrasp']['step'],
              result['finish'] and result['finish']['step'], flush=True)
    args.out.write_text(json.dumps(dict(robot_commands_sent=False, results=results), indent=2)+'\n')


if __name__ == '__main__':
    main()
