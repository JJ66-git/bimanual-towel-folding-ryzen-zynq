#!/usr/bin/env python3
"""Compare ACT first-grasp predictions from saved images and joint feedback."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

import cv2
import numpy as np


PROJECT = Path(__file__).resolve().parents[1]
POLICY = PROJECT / 'amd_hybrid_deploy_20261031/piper_towel_replica'
VIEWS = ('left', 'middle', 'right')
ARMS = {'left': (slice(0, 6), 6), 'right': (slice(7, 13), 13)}


def read_json(path):
    return json.loads(path.read_text())


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def read_rgb(path, frame_index):
    video = cv2.VideoCapture(str(path))
    try:
        video.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, bgr = video.read()
        if not ok:
            raise ValueError(f'Cannot read frame {frame_index} from {path}')
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    finally:
        video.release()


def field_sample(recording, frames, observations, row, state_names):
    tick = row['tick_monotonic_s']
    observation = min(observations, key=lambda sample: abs(sample['monotonic_s'] - tick))
    frame = min(frames, key=lambda sample: abs(sample['monotonic_s'] - tick))
    observation_delta = observation['monotonic_s'] - tick
    frame_delta = frame['monotonic_s'] - tick
    if abs(observation_delta) > .035 or abs(frame_delta) > .15:
        raise ValueError(f'No adequately timed recording at ACT refill step {row["step"]}')
    values = dict(zip(observation['feature_names'], observation['state'], strict=True))
    images = {view: read_rgb(recording / f'{view}.avi', frame['video_frame_index']) for view in VIEWS}
    state = np.asarray([values[name] for name in state_names], dtype=np.float32)
    return state, images, dict(frame=frame['video_frame_index'], frame_delta_ms=frame_delta*1000,
                               state_delta_ms=observation_delta*1000)


def difference(a, b):
    return {side: dict(joint_rms_rad=float(np.sqrt(np.mean((a[:, joints]-b[:, joints])**2))),
                       joint_max_abs_rad=float(np.abs(a[:, joints]-b[:, joints]).max()),
                       gripper_max_abs_mm=float(1000*np.abs(a[:, gripper]-b[:, gripper]).max()))
            for side, (joints, gripper) in ARMS.items()}


def neutral_background_level(rgb):
    pixels = rgb[::4, ::4].reshape(-1, 3).astype(np.float32)
    keep = pixels.max(axis=1) - pixels.min(axis=1) <= 28
    keep &= (pixels.min(axis=1) >= 100) & (pixels.max(axis=1) < 245)
    pixels = pixels[keep]
    if len(pixels) < 100:
        raise ValueError('Insufficient neutral pixels for brightness comparison')
    return np.median(pixels, axis=0)


def middle_table_boundary(rgb):
    """Locate a horizontal tabletop edge in the upper central region."""
    height, width = rgb.shape[:2]
    lo, hi = round(.075 * height), round(.15 * height)
    columns = rgb[lo:hi, width // 8 : -width // 8].astype(np.float32).mean(axis=2)
    split = np.argmax(columns[4:] - columns[:-4], axis=0)
    strength = (columns[4:] - columns[:-4])[split, np.arange(columns.shape[1])]
    valid = strength >= 35
    if valid.mean() < .8:
        return None
    points = lo + split[valid] + 4
    if np.ptp(np.percentile(points, [10, 90])) > 6:
        return None
    return int(np.percentile(points, 95))


def neutralize_static_background(rgb, initial, boundary):
    """Replace the visible static strip above the table; preserve foreground."""
    if boundary is None:
        return rgb
    current = rgb[:boundary]
    stationary = np.max(np.abs(current.astype(np.int16) - initial[:boundary]), axis=2) <= 18
    table = np.median(initial[boundary+6:boundary+14], axis=0).astype(np.uint8)
    adjusted = rgb.copy()
    adjusted[:boundary] = np.where(stationary[..., None], table[None, :, :], current)
    return adjusted


def load_cpu_act(reference_refills):
    sys.path[:0] = [str(POLICY/'runtime_source/src'), str(POLICY/'deployment_entry')]
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      TORCH_HOME=str(POLICY/'deployment_assets/torch'))
    import torch
    from lerobot.policies.act.configuration_act import ACTConfig
    from lerobot.policies.act.modeling_act import ACTPolicy
    from lerobot.policies.factory import make_pre_post_processors
    from lerobot.policies.utils import prepare_observation_for_inference
    from training_effort_adapter import install_training_effort_adapter
    from training_image_adapter import install_training_image_adapter

    torch.set_num_threads(4)
    model = POLICY/'models/act050000'
    context = read_json(reference_refills/'summary.json')['context']
    for filename, expected in context['model_files'].items():
        with (model/filename).open('rb') as handle:
            if hashlib.file_digest(handle, 'sha256').hexdigest() != expected:
                raise ValueError(f'ACT file differs from archived run: {filename}')

    config = ACTConfig.from_pretrained(str(model))
    config.device = 'cpu'
    policy = ACTPolicy.from_pretrained(str(model), config=config, strict=True, local_files_only=True).float().eval()
    pre, post = make_pre_post_processors(config, str(model),
                                         preprocessor_overrides={'device_processor': {'device': 'cpu'}})
    install_training_image_adapter(pre, POLICY/'configs/cobot_observation.json', model, 'act')
    initial, = reference_refills.glob('refill_*_step000000')
    names = read_json(initial/'report.json')['observation_names']
    install_training_effort_adapter(pre, names, 'act')
    device = torch.device('cpu')

    def predict(state, images):
        frame = {'observation.state': state.copy()}
        frame.update({f'observation.images.{view}': pixels.copy() for view, pixels in images.items()})
        with torch.inference_mode():
            batch = pre(prepare_observation_for_inference(frame, device, context['task'], 'piper_dual'))
            chunk = post(policy.predict_action_chunk(batch))[0, :config.n_action_steps].numpy()
        if not np.isfinite(chunk).all():
            raise ValueError('Non-finite ACT replay')
        return chunk

    return predict, config.n_action_steps, names, context


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-refills', type=Path, required=True)
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--recording', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--steps', type=int, nargs='+', default=(100, 150))
    parser.add_argument('--try-table-balance', action='store_true',
                        help='Replay a fixed per-view white-table balance estimated from start frames')
    parser.add_argument('--try-middle-background', action='store_true',
                        help='Replay suppression of the static background above a detected tabletop edge')
    parser.add_argument('--early-refill-offsets', type=int, nargs='*', default=(),
                        help='Try recomputing ACT from a later recorded observation within each chunk')
    args = parser.parse_args()
    predict, n_action_steps, names, context = load_cpu_act(args.reference_refills)
    if any(not 0 < offset < n_action_steps for offset in args.early_refill_offsets):
        parser.error('early refill offsets must be positive and shorter than the saved execution prefix')
    trace_rows = {row['step']: row for row in read_json(args.trace)['rows']}
    frames = read_jsonl(args.recording/'frame_times.jsonl')
    observations = [row for row in read_jsonl(args.recording/'robot_events.jsonl') if row['kind'] == 'observation']
    initial_middle = None
    boundary = None
    if args.try_middle_background:
        initial_middle = read_rgb(args.recording/'middle.avi', frames[0]['video_frame_index'])
        boundary = middle_table_boundary(initial_middle)
    gains = {}
    if args.try_table_balance:
        initial, = args.reference_refills.glob('refill_*_step000000')
        for view in VIEWS:
            before = cv2.cvtColor(cv2.imread(str(initial/f'{view}.png')), cv2.COLOR_BGR2RGB)
            after = cv2.cvtColor(cv2.imread(str(args.recording/f'first_{view}.png')), cv2.COLOR_BGR2RGB)
            gains[view] = np.clip(neutral_background_level(before)/neutral_background_level(after), .8, 1.25)

    reports = []
    for step in args.steps:
        directory, = args.reference_refills.glob(f'refill_*_step{step:06d}')
        report = read_json(directory/'report.json')
        if not report['complete'] or trace_rows[step].get('act_refill') is not True:
            raise ValueError(f'Both recordings need a complete ACT refill at step {step}')
        if report['observation_names'] != names:
            raise ValueError(f'Archived state names changed at step {step}')
        with np.load(directory/report['arrays_file'], allow_pickle=False) as arrays:
            reference_state = arrays['observation_state_raw']
            reference_images = {view: arrays[f'rgb_{view}'] for view in VIEWS}
            ref_recorded = arrays['candidate_prefix_unclipped']
        state, images, timing = field_sample(args.recording, frames, observations,
                                             trace_rows[step], names)
        reference = predict(reference_state, reference_images)
        field = predict(state, images)
        field_interval = [trace_rows[i] for i in range(step, step+n_action_steps)]
        field_action_key = ('act_before_projection'
                            if all('act_before_projection' in row for row in field_interval)
                            else 'act_action')
        field_recorded = np.asarray([row[field_action_key] for row in field_interval])
        variants = {'all_reference_images': predict(state, reference_images),
                    'reference_robot_state': predict(reference_state, images)}
        for view in VIEWS:
            variants[f'reference_{view}_image'] = predict(state, {**images, view: reference_images[view]})
        if gains:
            balanced = {view: np.clip(np.rint(images[view]*gains[view]), 0, 255).astype(np.uint8)
                        for view in VIEWS}
            variants['table_color_balance'] = predict(state, balanced)
            variants['middle_table_color_balance'] = predict(state, {**images, 'middle': balanced['middle']})
        if initial_middle is not None:
            corrected = neutralize_static_background(images['middle'], initial_middle, boundary)
            variants['middle_static_background'] = predict(state, {**images, 'middle': corrected})
            if gains:
                corrected = np.clip(np.rint(corrected*gains['middle']), 0, 255).astype(np.uint8)
                variants['middle_static_background_and_balance'] = predict(state, {**images, 'middle': corrected})
        replans = {}
        for offset in args.early_refill_offsets:
            next_step = step + offset
            next_state, next_images, next_timing = field_sample(args.recording, frames, observations,
                                                               trace_rows[next_step], names)
            new_chunk = predict(next_state, next_images)
            variant = np.concatenate((field[:offset], new_chunk[:n_action_steps-offset]))
            name = f'early_refill_plus_{offset}'
            variants[name] = variant
            prior_target = np.asarray(trace_rows[next_step]['previous'])
            replans[name] = dict(step=next_step, **next_timing,
                                 change_from_prior_dispatched_target=difference(new_chunk[:1], prior_target[None, :]),
                                 recorded_act_change_from_prior=difference(field_recorded[offset:offset+1], prior_target[None, :]))
        comparisons = {'reference_replay_error': difference(reference, ref_recorded),
                       'field_approximate_replay_error': difference(field, field_recorded),
                       'field_vs_reference': difference(field, reference),
                       'interventions': {name: {'change_from_field': difference(chunk, field),
                                                'difference_from_reference': difference(chunk, reference)}
                                         for name, chunk in variants.items()}}
        reports.append(dict(step=step, **timing, **comparisons, early_refills=replans,
                            field_recorded_action_key=field_action_key))
        print(f'ACT replay complete at step {step}', flush=True)
    output = dict(hardware_access=False, backend='cpu', weights_sha256=context['model_files']['model.safetensors'],
                  reference=str(args.reference_refills), field_recording=str(args.recording), refills=reports,
                  applied_gains={view: gain.tolist() for view, gain in gains.items()},
                  middle_table_boundary_px=boundary,
                  limitations=['Field images are nearest recorded 10 fps frames, not exact ACT inputs.',
                               'Cross-run swaps produce synthetic view/state combinations.',
                               'Action differences measure sensitivity, not contact, grasp success or closed-loop improvement.'])
    args.out.write_text(json.dumps(output, indent=2, allow_nan=False)+'\n')


if __name__ == '__main__':
    main()
