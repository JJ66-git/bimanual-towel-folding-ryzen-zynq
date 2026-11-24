#!/usr/bin/env python3
"""Evaluate observation-triggered ACT refills on saved towel-folding runs."""

import argparse
from bisect import bisect_right
import itertools
import json
from pathlib import Path

import cv2
import numpy as np

from audit_pregrasp_alignment import APPROACH_MAX_MM, APPROACH_MIN_MM, VISIBLE_FRACTION
from audit_pregrasp_alignment import approach_rows, inspect_frame
from replay_first_grasp import field_sample, load_cpu_act, read_json, read_jsonl


def far_edge(mask):
    width = mask.shape[1]
    edges = []
    for fraction in (.26, .56, .83):
        x = round(fraction * width)
        band = mask[:, x-12:x+12]
        occupied = np.flatnonzero((band > 0).mean(axis=1) > .42)
        if not occupied.size:
            return None
        edges.append(occupied[0])
    return np.asarray(edges)


def find_first_fold(recording, stamps, rows, closure, settings, cloth_features):
    """Require a stable, full-width inward edge after the first closure."""
    times = [row['tick_monotonic_s'] for row in rows]
    camera = cv2.VideoCapture(str(recording/'middle.avi'))
    baseline = first = previous = None
    try:
        for stamp in stamps:
            now = stamp['monotonic_s']
            step = bisect_right(times, now) - 1
            if baseline is not None and step < closure:
                continue
            # Stop after the first open/close cycle; a later cloth displacement
            # must not retroactively establish a first fold.
            if now - times[closure] > 5.5:
                return None
            camera.set(cv2.CAP_PROP_POS_FRAMES, stamp['video_frame_index'])
            ok, bgr = camera.read()
            if not ok:
                raise ValueError(f'Could not read saved middle frame {stamp["video_frame_index"]}')
            feature = cloth_features(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), settings)
            edge = None if feature is None else far_edge(feature['mask'])
            if baseline is None:
                if step >= closure:
                    return None
                if edge is None:
                    continue
                baseline = edge, feature['area']
                continue
            initial_edge, initial_area = baseline
            folded = (edge is not None and np.all(edge - initial_edge > .058 * bgr.shape[0])
                      and feature['area'] / initial_area < .77)
            if not folded:
                first = previous = None
                continue
            if previous is None or now - previous > .18:
                first = now
            previous = now
            if now - first >= .25:
                return {'step': max(0, step), 'frame': stamp['video_frame_index'],
                        'observed_at': now, 'area_ratio': feature['area'] / initial_area,
                        'far_edge_shift_px': (edge - initial_edge).tolist()}
    finally:
        camera.release()
    return None


def wrist_evidence(recording, side, stamps, rows, begin, end, index):
    """Use distinct saved frames observed before the closure request."""
    times = [row['tick_monotonic_s'] for row in rows]
    camera = cv2.VideoCapture(str(recording/f'{side}.avi'))
    samples = []
    try:
        for stamp in stamps:
            now = stamp['monotonic_s']
            if now < times[begin]:
                continue
            if now >= times[end]:
                break
            step = bisect_right(times, now) - 1
            width = rows[step]['measured'][index] * 1000
            if not APPROACH_MIN_MM <= width <= APPROACH_MAX_MM:
                continue
            camera.set(cv2.CAP_PROP_POS_FRAMES, stamp['video_frame_index'])
            ok, image = camera.read()
            if not ok:
                raise ValueError(f'Could not read saved {side} frame {stamp["video_frame_index"]}')
            coverage = max(inspect_frame(image)['corridor_coverage'])
            samples.append({'step': step, 'time': now, 'corridor_coverage': coverage})
    finally:
        camera.release()
    return samples


def reopen_episode(rows, folded):
    """Find the first right approach from the observed folded shape."""
    start = folded['step']
    for is_open, group in itertools.groupby(rows[start:], key=lambda row: row['target'][13] >= .025):
        episode = list(group)
        if is_open and len(episode) >= 5 and episode[-1]['step'] < len(rows)-1:
            return episode[0]['step'], episode[-1]['step'] + 1
    return None


def test_refill(step, index, min_width, rows, predict, recording, stamps, observations, names):
    state, images, timing = field_sample(recording, stamps, observations, rows[step], names)
    chunk = predict(state, images)
    previous = np.asarray(rows[step]['previous'])
    measured = np.asarray(rows[step]['measured'])
    joints = [0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12]
    width = np.clip(chunk[:, index], 0, .08) * 1000
    previous_opening = previous[index] * 1000
    recorded = np.array([row['target'][index] * 1000 for row in rows[step:step+len(chunk)]])
    first_approach_open_ticks = next((i for i, value in enumerate(width) if value < min_width), len(width))
    first_jump = float(np.max(np.abs(chunk[0, joints] - previous[joints])))
    first_tracking = float(np.max(np.abs(chunk[0, joints] - measured[joints])))
    return {
        'step': step, 'recording_timing': timing,
        'recorded_width_next_10_mm': recorded[:10].tolist(),
        'candidate_width_next_10_mm': width[:10].tolist(),
        'recorded_peak_mm': float(recorded.max()), 'candidate_peak_mm': float(width.max()),
        'candidate_holds_open_for_10_ticks': first_approach_open_ticks >= 10,
        'candidate_initial_open_ticks': first_approach_open_ticks,
        'candidate_open_threshold_mm': min_width,
        'previous_command_mm': float(previous_opening),
        'candidate_first_joint_change_rad': first_jump,
        'candidate_first_tracking_change_rad': first_tracking,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-refills', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    manifest = read_json(args.manifest)
    settings = read_json(Path(manifest['segmentation_profile']))['segmentation']
    predict, _, names, _ = load_cpu_act(args.reference_refills)
    from towel_quality import cloth_features

    results = []
    for run in manifest['runs']:
        rows = read_json(Path(run['trace']))['rows']
        recording = Path(run['recording'])
        stamps = read_jsonl(recording/'frame_times.jsonl')
        observations = [row for row in read_jsonl(recording/'robot_events.jsonl')
                        if row['kind'] == 'observation']
        result = {'label': run['label'], 'outcome': run['outcome'], 'first_grasp': {}}
        closures = []
        for side, index in (('left', 6), ('right', 13)):
            closure, approach = approach_rows(rows, index)
            if closure is None or not approach:
                continue
            closures.append(closure)
            samples = wrist_evidence(recording, side, stamps, rows, approach[0]['step'], closure, index)
            recent = samples[-2:]
            missed = (len(recent) == 2 and recent[-1]['time'] - recent[0]['time'] <= .18
                      and rows[closure]['tick_monotonic_s'] - recent[-1]['time'] <= .30
                      and all(sample['corridor_coverage'] < VISIBLE_FRACTION for sample in recent))
            event = {'closure_step': closure, 'samples': samples, 'empty_approach': missed}
            if missed:
                event['refill'] = test_refill(closure, index, 25, rows, predict, recording,
                                              stamps, observations, names)
            result['first_grasp'][side] = event

        fold = (find_first_fold(recording, stamps, rows, max(closures), settings, cloth_features)
                if len(closures) == 2 else None)
        result['first_fold_observation'] = fold
        episode = reopen_episode(rows, fold) if fold is not None else None
        if episode is not None:
            start, close = episode
            peak = max(row['measured'][13] * 1000 for row in rows[start:close])
            duration = rows[close]['tick_monotonic_s'] - rows[start]['tick_monotonic_s']
            regrasp = {'open_step': start, 'closing_step': close,
                       'peak_feedback_mm': peak, 'commanded_open_s': duration}
            if peak < 40 or duration < 1:
                observed_fold = next(i for i in range(fold['step'], close+1)
                                     if rows[i]['tick_monotonic_s'] >= fold['observed_at'])
                observed_open = next((i for i in range(start, close)
                                      if rows[i]['measured'][13] >= .025), close)
                triggers = {
                    'stable_fold_observed': observed_fold,
                    'first_open_feedback': observed_open,
                    'model_starts_closing': close,
                }
                evaluations = {
                    step: test_refill(step, 13, 35, rows, predict, recording, stamps, observations, names)
                    for step in dict.fromkeys(triggers.values())
                }
                regrasp['refill_trials'] = {
                    trigger: evaluations[step] for trigger, step in triggers.items()
                }
            result['right_regrasp'] = regrasp
        results.append(result)
        print(run['label'], 'first fold', fold is not None,
              'right reopen', (None if episode is None else round(peak, 1)),
              'first approach alerts', [side for side, row in result['first_grasp'].items()
                                        if row['empty_approach']], flush=True)
    args.out.write_text(json.dumps({'execution': 'recording_only', 'runs': results,
                                    'limitations': [
                                        'The fold cue is based on silhouette and does not certify layers.',
                                        'Recorded RGB is sampled at about 10 Hz. Policy replay uses nearest saved input.',
                                        'The counterfactual refill does not produce new physical contact or subsequent images.',
                                    ]}, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
