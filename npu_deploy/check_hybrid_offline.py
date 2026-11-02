#!/usr/bin/env python3
"""Replay a fixed ACT/SmolVLA observation through the project's blend, without devices."""
import argparse
import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
B = ROOT / 'amd_hybrid_deploy_20261031'
P = B / 'piper_towel_replica'
sys.path[:0] = [str(P / 'runtime_source/src'), str(P / 'deployment_entry'), str(ROOT / 'npu_deploy')]
os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TORCH_HOME=str(P / 'deployment_assets/torch'))

import numpy as np
import torch
from PIL import Image
from lerobot.configs import RTCAttentionSchedule
from lerobot.policies.act.configuration_act import ACTConfig
from lerobot.policies.act.modeling_act import ACTPolicy
from lerobot.policies.factory import make_pre_post_processors
from lerobot.policies.rtc.configuration_rtc import RTCConfig
from lerobot.policies.utils import prepare_observation_for_inference
from portable_backend import select_device, seed_all, smol_config, place_policy
from training_effort_adapter import STATE_NAMES, install_training_effort_adapter
from training_image_adapter import install_training_image_adapter
from cobot_motion_adapter import load_motion_profile, configure_act, configure_bounds, constrain_correction
from hybrid_entry import NpuSmolLoader
import cobot_hybrid as hybrid
import blend_core as core


def controller_args():
    # Derive the same controller options as the deployed hybrid launcher.
    output = subprocess.check_output([sys.executable, str(ROOT / 'npu_deploy/launch_hybrid.py'),
                                      '--check-only'], text=True)
    launch = json.loads(output)
    saved = sys.argv
    try:
        sys.argv = ['cobot_hybrid.py', *launch[launch.index('--') + 1:]]
        return hybrid.parse_args()
    finally:
        sys.argv = saved


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--steps', type=int, default=20)
    p.add_argument('--threads', type=int, default=4)
    p.add_argument('--out', type=Path, default=ROOT/'npu_deploy/artifacts/smol_vision/hybrid_offline.json')
    p.add_argument('--fixture', type=Path, default=B/'fixtures/observation.json')
    a = p.parse_args()
    if not 1 <= a.steps <= 50 or a.threads < 1:
        p.error('steps must be 1–50 and threads must be positive')
    torch.set_num_threads(a.threads)
    args = controller_args()
    motion = load_motion_profile(args.motion_profile, core)
    configure_bounds(motion, core)
    device = select_device(args.backend)
    fixture = json.loads(a.fixture.read_text())
    state = dict(zip(fixture['feature_names'], fixture['state'], strict=True))
    if set(state) != set(STATE_NAMES):
        raise ValueError('Fixture state mismatch')
    frame = {'observation.state': np.asarray([state[k] for k in STATE_NAMES], dtype=np.float32)}
    for role, name in fixture['images'].items():
        frame['observation.images.' + role] = np.asarray(Image.open(a.fixture.parent/name).convert('RGB'))
    initial = np.asarray([state[k] for k in core.ACTION_NAMES], dtype=np.float32)

    def processors(policy, path, label):
        pre, post = make_pre_post_processors(policy.config, str(path),
                      preprocessor_overrides={'device_processor': {'device': str(device)}})
        install_training_image_adapter(pre, P/'configs/cobot_observation.json', path, label)
        install_training_effort_adapter(pre, STATE_NAMES, label)
        return pre, post

    def infer(policy, pre, post, prefix=None):
        seed_all(args.seed, device)
        policy.reset()
        batch = pre(prepare_observation_for_inference(copy.deepcopy(frame), device, fixture['task'], 'piper_dual'))
        kw = {} if prefix is None else {'prev_chunk_left_over': prefix[:, 4:].clone(),
                                       'inference_delay': 4, 'execution_horizon': args.execution_horizon}
        start = time.perf_counter()
        norm = policy.predict_action_chunk(batch, **kw)
        result = post(norm)
        if device.type == 'cuda':
            torch.cuda.synchronize()
        elapsed = time.perf_counter()-start
        if not bool(torch.isfinite(result).all()):
            raise ValueError('Non-finite policy action')
        return norm.detach().clone(), result.detach().float().cpu(), elapsed

    def replay(act, smol):
        previous = initial.copy()
        correction = np.zeros(14, dtype=np.float32)
        authority = 0.0
        rows = []
        for i in range(a.steps):
            try:
                act_action = hybrid.as_action(act[0, i].clone(), project_soft_limits=False)
                candidate = hybrid.as_action(smol[0, i].clone(), project_soft_limits=True)
                filtered = (previous + args.smol_lowpass_alpha * (candidate - previous)).astype(np.float32)
                filtered_step = float(np.max(np.abs(filtered[core.ARM] - previous[core.ARM])))
                filtered_disagreement = float(np.max(np.abs(filtered[core.ARM] - act_action[core.ARM])))
                old_correction = correction.copy()
                result = core.blend_step(act_action, candidate, previous, correction, authority, args)
                authority = result.authority
                correction = constrain_correction(act_action, result.correction, old_correction, args, core)
                target = act_action.copy()
                target[core.ARM] += correction[core.ARM]
                target[core.GRIPPER] = act_action[core.GRIPPER]
                target = core.apply_global_smoothing(target, previous, act_action[core.GRIPPER],
                                                     args.global_lowpass_alpha)
                core.validate_absolute(target)
                rows.append({'source': result.source, 'detail': result.detail, 'authority': authority,
                             'filtered_step_rad': filtered_step, 'filtered_act_disagreement_rad': filtered_disagreement,
                             'target': target.tolist(), 'act_gripper': act_action[core.GRIPPER].tolist()})
                previous = target.copy()  # idealized command feedback, not a measured robot state
            except (ValueError, RuntimeError) as exc:
                return {'rows': rows, 'stopped_at': i, 'error': str(exc)}
        return {'rows': rows, 'stopped_at': None, 'error': None}

    try:
        seed_all(args.seed, device)
        cfg = configure_act(ACTConfig.from_pretrained(str(args.act_model)), motion)
        cfg.device = str(device)
        act = ACTPolicy.from_pretrained(str(args.act_model), config=cfg, strict=True)
        place_policy(act, device, args.precision)
        act_pre, act_post = processors(act, args.act_model, 'act')
        _, act_chunk, act_s = infer(act, act_pre, act_post)
        print(f'ACT_READY seconds={act_s:.3f}', flush=True)

        start = time.perf_counter()
        smol = NpuSmolLoader.from_pretrained(str(args.smol_model), config=smol_config(args.smol_model, device))
        startup_s = time.perf_counter()-start
        place_policy(smol, device, args.precision)
        smol_pre, smol_post = processors(smol, args.smol_model, 'smolvla')
        vision = smol.model.vlm_with_expert
        npu_embed = vision.embed_image
        native_embed = type(vision).embed_image.__get__(vision, type(vision))
        vision.embed_image = native_embed
        prefix, _, _ = infer(smol, smol_pre, smol_post)
        smol.config.rtc_config = RTCConfig(enabled=True, mode='guided',
            prefix_attention_schedule=RTCAttentionSchedule.EXP, max_guidance_weight=args.guidance_weight,
            execution_horizon=args.execution_horizon, debug=False)
        smol.init_rtc_processor()
        _, native, native_s = infer(smol, smol_pre, smol_post, prefix)
        vision.embed_image = npu_embed
        _, npu, npu_s = infer(smol, smol_pre, smol_post, prefix)
        native_replay, npu_replay = replay(act_chunk, native), replay(act_chunk, npu)
        count = min(len(native_replay['rows']), len(npu_replay['rows']))
        target_delta = None
        decisions_differ = None
        if count:
            target_delta = np.max(np.abs(np.asarray([r['target'] for r in native_replay['rows'][:count]]) -
                                         np.asarray([r['target'] for r in npu_replay['rows'][:count]])), axis=0).tolist()
            decisions_differ = sum(x['source'] != y['source'] for x, y in
                                   zip(native_replay['rows'][:count], npu_replay['rows'][:count], strict=True))
        report = {'qualification': 'One fixed observation, idealized previous command as feedback, no asynchronous queue or hardware. Not closed-loop validation.',
                  'fixture': str(a.fixture.resolve()), 'fixture_note': fixture.get('source_kind'),
                  'npu_loader': 'hybrid_entry.NpuSmolLoader', 'steps_requested': a.steps,
                  'act_inference_s': act_s, 'npu_startup_s': startup_s,
                  'native_guided_s': native_s, 'npu_guided_s': npu_s,
                  'matched_steps': count, 'source_decisions_differ': decisions_differ,
                  'max_target_abs_difference_per_dim': target_delta,
                  'native': native_replay, 'npu': npu_replay}
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
        print('HYBRID_OFFLINE=' + json.dumps({k: v for k, v in report.items() if k not in ('native', 'npu')}), flush=True)
        print(f"NATIVE_REPLAY=steps:{len(native_replay['rows'])} error:{native_replay['error']}", flush=True)
        print(f"NPU_REPLAY=steps:{len(npu_replay['rows'])} error:{npu_replay['error']}", flush=True)
        print('HYBRID_REPORT=' + str(a.out), flush=True)
        if native_replay['error'] or npu_replay['error']:
            raise RuntimeError('Offline replay stopped early; inspect the saved report')
    finally:
        for client in NpuSmolLoader.clients:
            client.close()
        NpuSmolLoader.clients.clear()


if __name__ == '__main__':
    main()
