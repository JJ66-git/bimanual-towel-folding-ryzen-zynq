#!/usr/bin/env python3
"""Offline comparison of the trained SmolVLA checkpoint against FastFlowLM NPU."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
B = ROOT/'amd_hybrid_deploy_20261031'
P = B/'piper_towel_replica'
ARTIFACT = ROOT/'npu_deploy/artifacts/fastflow_custom'
sys.path[:0] = [str(ROOT/'npu_deploy/fastflow_runtime'), str(P/'runtime_source/src'), str(P/'deployment_entry')]
os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TORCH_HOME=str(P/'deployment_assets/torch'))
os.environ['SMOLVLA_XCLBIN_DIR'] = str(ROOT/'npu_deploy/fastflow_runtime/smolvla_npu_pkg/XCLBINS')

import numpy as np
import torch
from lerobot.policies.factory import make_pre_post_processors
from portable_backend import smol_config
import smolvla_npu


def main():
    # Keep the already-loaded XDNA plugin out of any compiler/helper processes.
    os.environ.pop('LD_PRELOAD', None)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model-dir', type=Path, default=ARTIFACT)
    p.add_argument('--reference-file', type=Path, default=ARTIFACT/'fixture_reference.npz')
    p.add_argument('--warmup', type=int, default=1)
    p.add_argument('--repeat', type=int, default=3)
    args = p.parse_args()
    if args.warmup < 0 or args.repeat < 1:
        p.error('warmup must be nonnegative; repeat must be positive')
    torch.set_num_threads(4)
    checkpoint = P/'models/smol030000'
    cfg = smol_config(checkpoint, torch.device('cpu'))
    _, post = make_pre_post_processors(cfg, str(checkpoint),
        preprocessor_overrides={'device_processor': {'device': 'cpu'}})
    data = np.load(args.reference_file, allow_pickle=False)
    images = [torch.from_numpy(data[f'image_{i}']).reshape(-1).contiguous() for i in range(3)]
    tokens = torch.from_numpy(data['tokens']).reshape(-1).contiguous()
    state = torch.from_numpy(data['state']).reshape(-1).contiguous()
    noise = torch.from_numpy(data['noise']).reshape(-1).contiguous()
    # The exported SmolVLM connector produces 64 tokens per image; this saved
    # policy has no extra image-special tokens, and appends one state token.
    prefix_len = len(images) * 64 + tokens.numel() + 1
    if len(images) > smolvla_npu.MAX_IMAGES or prefix_len > smolvla_npu.MAX_PREFIX_LEN:
        raise RuntimeError(f'FastFlow input exceeds compiled prefix capacity: {prefix_len}')
    print(f'FASTFLOW_PREFIX={prefix_len}/{smolvla_npu.MAX_PREFIX_LEN}', flush=True)
    started = time.perf_counter()
    npu = smolvla_npu.SmolVLA(str(args.model_dir))
    init_s = time.perf_counter()-started
    print('FASTFLOW_MODEL=', repr(npu), 'chunk_size=', npu.chunk_size, 'action_dim=', npu.action_dim, flush=True)
    if npu.chunk_size != 50 or npu.action_dim != 32:
        raise RuntimeError(f'Unexpected FastFlow model shape: {npu.chunk_size}x{npu.action_dim}')
    elapsed = []
    for index in range(args.warmup+args.repeat):
        start = time.perf_counter()
        result = npu.forward(images, tokens, state, noise)
        seconds = time.perf_counter()-start
        if index >= args.warmup:
            elapsed.append(seconds)
        print(f'FASTFLOW_SAMPLE={index+1} elapsed={seconds:.3f}', flush=True)
    result = torch.as_tensor(result).reshape(1, 50, 32)[..., :14].float()
    if not torch.isfinite(result).all():
        raise RuntimeError('FastFlow returned non-finite actions')
    physical = post(result).detach().float().cpu().numpy()
    normalized = result.detach().float().cpu().numpy()
    report = {'checkpoint': str(checkpoint), 'model_path': str(args.model_dir), 'reference_file':str(args.reference_file), 'init_s': init_s,
              'warmup': args.warmup, 'repeat': args.repeat, 'seconds': elapsed,
              'p50_s': float(np.percentile(elapsed, 50)), 'p95_s': float(np.percentile(elapsed, 95)),
              'qualification': 'Offline full plain SmolVLA, same fixed observation and noise; does not exercise guided RTC, robot, or closed-loop control.'}
    for kind, actual in [('normalized', normalized), ('physical', physical)]:
        reference = data['action_'+kind]
        error = np.abs(actual-reference)
        report[kind] = {'max_abs': float(error.max()), 'mean_abs': float(error.mean()),
                        'max_abs_per_action_dim': np.max(error, axis=(0,1)).tolist()}
    suffix = '_rocm' if args.reference_file.stem.endswith('_rocm') else ''
    np.savez_compressed(args.model_dir/f'fastflow_outputs{suffix}.npz', normalized=normalized, physical=physical)
    (args.model_dir/f'fastflow_check{suffix}.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    print('FASTFLOW_REPORT='+json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
