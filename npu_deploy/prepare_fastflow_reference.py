#!/usr/bin/env python3
"""Prepare exact project inputs and CPU reference for FastFlowLM plain inference."""
import argparse
import copy
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
B = ROOT/'amd_hybrid_deploy_20261031'
P = B/'piper_towel_replica'
sys.path[:0] = [str(P/'runtime_source/src'), str(P/'deployment_entry')]
os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TORCH_HOME=str(P/'deployment_assets/torch'))

import numpy as np
import torch
from PIL import Image
from lerobot.policies.factory import make_pre_post_processors
from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy
from lerobot.policies.utils import prepare_observation_for_inference
from lerobot.utils.constants import OBS_LANGUAGE_TOKENS
from portable_backend import place_policy, select_device, smol_config
from training_effort_adapter import STATE_NAMES, install_training_effort_adapter
from training_image_adapter import install_training_image_adapter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['cpu', 'rocm'], default='cpu')
    parser.add_argument('--warmup', type=int, default=0)
    parser.add_argument('--repeat', type=int, default=1)
    args = parser.parse_args()
    if args.warmup < 0 or args.repeat < 1:
        parser.error('warmup must be nonnegative and repeat positive')
    torch.set_num_threads(4)
    device = select_device(args.backend)
    checkpoint = P/'models/smol030000'
    policy = SmolVLAPolicy.from_pretrained(str(checkpoint), config=smol_config(checkpoint, device), strict=True)
    place_policy(policy, device, 'auto')
    pre, post = make_pre_post_processors(policy.config, str(checkpoint),
        preprocessor_overrides={'device_processor': {'device': str(device)}})
    install_training_image_adapter(pre, P/'configs/cobot_observation.json', checkpoint, 'smolvla')
    install_training_effort_adapter(pre, STATE_NAMES, 'smolvla')
    fixture = json.loads((B/'fixtures/observation.json').read_text())
    state = dict(zip(fixture['feature_names'], fixture['state'], strict=True))
    if set(state) != set(STATE_NAMES):
        raise ValueError('Fixture state mismatch')
    frame = {'observation.state': np.asarray([state[k] for k in STATE_NAMES], dtype=np.float32)}
    for role, name in fixture['images'].items():
        frame['observation.images.'+role] = np.asarray(Image.open(B/'fixtures'/name).convert('RGB'))
    batch = pre(prepare_observation_for_inference(copy.deepcopy(frame), device, fixture['task'], 'piper_dual'))
    images, masks = policy.prepare_images(batch)
    if len(images) != 3 or any(not bool(x.all()) for x in masks):
        raise RuntimeError('Expected three present camera images')
    state = policy.prepare_state(batch)
    tokens = batch[OBS_LANGUAGE_TOKENS]
    noise = torch.randn((1, policy.config.chunk_size, policy.config.max_action_dim),
                        generator=torch.Generator(device='cpu').manual_seed(42), dtype=torch.float32).to(device)
    elapsed = []
    with torch.no_grad():
        for i in range(args.warmup+args.repeat):
            policy.reset()
            if device.type == 'cuda': torch.cuda.synchronize()
            start = time.perf_counter()
            actions = policy.predict_action_chunk(copy.deepcopy(batch), noise=noise)
            if device.type == 'cuda': torch.cuda.synchronize()
            seconds = time.perf_counter() - start
            if i >= args.warmup: elapsed.append(seconds)
            print(f'REFERENCE_SAMPLE={i+1} elapsed={seconds:.3f}', flush=True)
        physical = post(actions)
    out = ROOT/'npu_deploy/artifacts/fastflow_custom'
    out.mkdir(parents=True, exist_ok=True)
    refpath = out/('fixture_reference.npz' if args.backend == 'cpu' else 'fixture_reference_rocm.npz')
    np.savez_compressed(refpath,
        **{f'image_{i}': image.detach().float().cpu().numpy() for i, image in enumerate(images)},
        state=state.detach().float().cpu().numpy(), tokens=tokens.cpu().numpy(), noise=noise.cpu().numpy(),
        action_normalized=actions.detach().float().cpu().numpy(), action_physical=physical.detach().float().cpu().numpy())
    report = {'backend':args.backend, 'warmup':args.warmup, 'repeat':args.repeat, 'seconds':elapsed,
              'p50_s':float(np.percentile(elapsed,50)), 'p95_s':float(np.percentile(elapsed,95)),
              'qualification':'Model forward on fixed preprocessed input, includes policy image preparation; no robot.'}
    (out/f'reference_{args.backend}.json').write_text(json.dumps(report, indent=2)+'\n')
    print(f'REFERENCE={refpath}')
    print('REFERENCE_REPORT='+json.dumps(report), flush=True)
    print('input shapes:', [list(x.shape) for x in images], 'state:', list(state.shape),
          'tokens:', list(tokens.shape), 'noise:', list(noise.shape), 'actions:', list(actions.shape))


if __name__ == '__main__':
    main()
