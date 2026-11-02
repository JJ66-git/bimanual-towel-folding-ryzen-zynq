#!/usr/bin/env python3
"""Offline native-vs-worker SmolVLA plain and guided action comparison; no robot access."""
import argparse
import copy
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
B = ROOT/'amd_hybrid_deploy_20261031'
P = B/'piper_towel_replica'
sys.path[:0] = [str(P/'runtime_source/src'), str(P/'deployment_entry'), str(ROOT/'npu_deploy')]
os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TORCH_HOME=str(P/'deployment_assets/torch'))

import numpy as np
import torch
from PIL import Image
from lerobot.configs import RTCAttentionSchedule
from lerobot.policies.factory import make_pre_post_processors
from lerobot.policies.rtc.configuration_rtc import RTCConfig
from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy
from lerobot.policies.utils import prepare_observation_for_inference
from portable_backend import select_device, seed_all, smol_config, place_policy
from training_effort_adapter import STATE_NAMES, install_training_effort_adapter
from training_image_adapter import install_training_image_adapter
from vision_client import VisionClient


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['cpu', 'rocm'], default='cpu')
    parser.add_argument('--worker', choices=['cpu', 'npu'], required=True)
    parser.add_argument('--warmup', type=int, default=0)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--artifact-dir', type=Path, default=ROOT/'npu_deploy/artifacts/smol_vision')
    parser.add_argument('--fixture', type=Path, default=B/'fixtures/observation.json')
    parser.add_argument('--output-stem', help='Optional report filename stem in artifact-dir')
    args = parser.parse_args()
    if args.repeat < 1 or args.warmup < 0 or args.threads < 1:
        parser.error('repeat/threads must be positive and warmup nonnegative')
    torch.set_num_threads(args.threads)
    device = select_device(args.backend)
    checkpoint = P/'models/smol030000'
    export = json.loads((args.artifact_dir/'export.json').read_text())
    for path, key in ((checkpoint/'model.safetensors', 'weights_sha256'),
                      (checkpoint/'config.json', 'config_sha256'),
                      (args.artifact_dir/'smol_vision.onnx', 'onnx_sha256')):
        if digest(path) != export[key]:
            raise ValueError(f'Artifact is not paired with the current checkpoint: {path}')
    policy = SmolVLAPolicy.from_pretrained(str(checkpoint), config=smol_config(checkpoint, device), strict=True)
    place_policy(policy, device, 'auto')
    pre, post = make_pre_post_processors(policy.config, str(checkpoint),
        preprocessor_overrides={'device_processor': {'device': str(device)}})
    install_training_image_adapter(pre, P/'configs/cobot_observation.json', checkpoint, 'smolvla')
    install_training_effort_adapter(pre, STATE_NAMES, 'smolvla')
    fixture = json.loads(args.fixture.read_text())
    state = dict(zip(fixture['feature_names'], fixture['state'], strict=True))
    if set(state) != set(STATE_NAMES):
        raise ValueError('Fixture state mismatch')
    frame = {'observation.state': np.asarray([state[k] for k in STATE_NAMES], dtype=np.float32)}
    for role, name in fixture['images'].items():
        frame['observation.images.' + role] = np.asarray(Image.open(args.fixture.parent/name).convert('RGB'))
    vision = policy.model.vlm_with_expert
    original_embed = vision.embed_image
    dtype = next(vision.get_vlm_model().vision_model.parameters()).dtype

    def sync():
        if device.type == 'cuda':
            torch.cuda.synchronize()

    def measure(mode, prefix=None):
        times, grad_count = [], 0
        initial_grad = torch.autograd.grad
        def counted_grad(*a, **kw):
            nonlocal grad_count
            grad_count += 1
            return initial_grad(*a, **kw)
        torch.autograd.grad = counted_grad
        try:
            for i in range(args.warmup + args.repeat):
                seed_all(42, device)
                policy.reset()
                sync()
                started = time.perf_counter()
                batch = pre(prepare_observation_for_inference(copy.deepcopy(frame), device, fixture['task'], 'piper_dual'))
                kw = {} if prefix is None else {'prev_chunk_left_over': prefix[:,4:].clone(),
                    'inference_delay': 4, 'execution_horizon': 10}
                normalized = policy.predict_action_chunk(batch, **kw)
                output = post(normalized)
                sync()
                elapsed = time.perf_counter() - started
                if i >= args.warmup:
                    times.append(elapsed)
                if output.shape != (1,50,14) or not torch.isfinite(output).all():
                    raise RuntimeError(f'{mode}: invalid action output')
                print(f'{mode} sample={i+1} elapsed={elapsed:.3f}s', flush=True)
        finally:
            torch.autograd.grad = initial_grad
        return normalized.detach().clone(), output.detach().float().cpu().numpy(), times, grad_count

    seed_all(42, device)
    prefix, native_plain, plain_times, _ = measure('native_plain')
    policy.config.rtc_config = RTCConfig(enabled=True, mode='guided', prefix_attention_schedule=RTCAttentionSchedule.EXP,
                                         max_guidance_weight=2.5, execution_horizon=10, debug=False)
    policy.init_rtc_processor()
    _, native_guided, guided_times, native_grads = measure('native_guided', prefix)
    if not native_grads:
        raise RuntimeError('Native guided gradient path was not exercised')
    started = time.perf_counter()
    with VisionClient(args.worker, args.artifact_dir/'smol_vision.onnx', dtype) as client:
        startup_s = time.perf_counter() - started
        try:
            vision.embed_image = client
            policy.config.rtc_config = None
            policy.init_rtc_processor()
            _, candidate_plain, candidate_plain_times, _ = measure('worker_plain')
            policy.config.rtc_config = RTCConfig(enabled=True, mode='guided', prefix_attention_schedule=RTCAttentionSchedule.EXP,
                max_guidance_weight=2.5, execution_horizon=10, debug=False)
            policy.init_rtc_processor()
            _, candidate_guided, candidate_guided_times, worker_grads = measure('worker_guided', prefix)
        finally:
            vision.embed_image = original_embed
    if not worker_grads:
        raise RuntimeError('Worker guided gradient path was not exercised')
    report = {'backend': args.backend, 'vision_worker': args.worker, 'worker_startup_s': startup_s,
              'torch': torch.__version__, 'hip': torch.version.hip, 'checkpoint_sha256': export['weights_sha256'],
              'onnx_sha256': export['onnx_sha256'],
              'warmup': args.warmup, 'repeat': args.repeat, 'native_guided_autograd_calls': native_grads,
              'worker_guided_autograd_calls': worker_grads,
              'qualification': 'One fixed offline fixture; no robot. Worker provider registration is not proof of NPU execution; inspect logs. Compare joint/clip errors and full controller before use.'}
    report['fixture'] = str(args.fixture.resolve())
    report['fixture_note'] = fixture.get('source_kind')
    arrays = {}
    for mode, ref, actual, rt, ct in (('plain', native_plain, candidate_plain, plain_times, candidate_plain_times),
                                     ('guided', native_guided, candidate_guided, guided_times, candidate_guided_times)):
        err = np.abs(actual-ref)
        report[mode] = {'max_abs': float(err.max()), 'mean_abs': float(err.mean()),
                        'max_abs_per_action_dim': np.max(err, axis=(0,1)).tolist(),
                        'native_seconds': rt, 'worker_seconds': ct}
        arrays[f'native_{mode}'] = ref
        arrays[f'worker_{mode}'] = actual
    stem = args.output_stem or f'policy_{args.backend}_{args.worker}'
    if Path(stem).name != stem or stem in ('.', '..'):
        raise ValueError('output-stem must be a filename stem')
    path = args.artifact_dir/f'{stem}.json'
    path.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    np.savez_compressed(path.with_suffix('.npz'), **arrays)
    print('POLICY_REPORT='+json.dumps(report), flush=True)
    print('POLICY_REPORT_PATH='+str(path), flush=True)


if __name__ == '__main__':
    main()
