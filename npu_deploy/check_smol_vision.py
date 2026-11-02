#!/usr/bin/env python3
"""Compare an exported SmolVLA encoder against its three PyTorch reference images."""
import argparse
import json
import os
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort


def main():
    # The XDNA plugin is loaded at process startup via LD_PRELOAD. Do not pass
    # that preload to the compiler's shell/tools, which may use a clean library
    # search path and fail before compilation finishes.
    os.environ.pop('LD_PRELOAD', None)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--artifact-dir', type=Path, default=Path(__file__).parent/'artifacts/smol_vision')
    p.add_argument('--provider', choices=['cpu', 'npu'], required=True)
    p.add_argument('--warmup', type=int, default=1)
    p.add_argument('--repeat', type=int, default=3)
    args = p.parse_args()
    if args.repeat < 1 or args.warmup < 0:
        p.error('repeat must be positive and warmup nonnegative')
    if args.provider == 'npu' and 'VitisAIExecutionProvider' not in ort.get_available_providers():
        raise RuntimeError('VitisAIExecutionProvider is not installed')
    providers = ['VitisAIExecutionProvider', 'CPUExecutionProvider'] if args.provider == 'npu' else ['CPUExecutionProvider']
    options = ort.SessionOptions()
    options.log_severity_level = 1 if args.provider == 'npu' else 2
    started = time.perf_counter()
    session = ort.InferenceSession(str(args.artifact_dir/'smol_vision.onnx'), sess_options=options, providers=providers)
    load_s = time.perf_counter() - started
    if args.provider == 'npu' and 'VitisAIExecutionProvider' not in session.get_providers():
        raise RuntimeError('Requested NPU provider was not registered')
    fixture = np.load(args.artifact_dir/'fixture.npz', allow_pickle=False)
    report = {'provider_requested': args.provider, 'providers_registered': session.get_providers(),
              'session_create_s': load_s, 'warmup': args.warmup, 'repeat': args.repeat,
              'onnxruntime': ort.__version__, 'images': [],
              'qualification': 'Offline single-fixture encoder comparison. Provider registration alone does not prove NPU execution; inspect compiler logs and complete action/controller validation.'}
    output_arrays = {}
    for i in range(3):
        pixels = np.asarray(fixture[f'image_{i}'], dtype=np.float32)
        reference = np.asarray(fixture[f'feature_{i}'], dtype=np.float32)
        timings = []
        for j in range(args.warmup + args.repeat):
            start = time.perf_counter()
            result = np.asarray(session.run(['image_features'], {'pixel_values': pixels})[0], dtype=np.float32)
            elapsed = time.perf_counter() - start
            if j >= args.warmup:
                timings.append(elapsed)
        if result.shape != reference.shape or not np.isfinite(result).all():
            raise RuntimeError(f'image {i}: shape mismatch or non-finite output')
        delta = np.abs(result - reference)
        report['images'].append({'index': i, 'shape': list(result.shape), 'max_abs': float(delta.max()),
            'mean_abs': float(delta.mean()), 'rmse': float(np.sqrt(np.mean(delta**2))),
            'reference_max_abs': float(np.abs(reference).max()),
            'seconds': timings, 'p50_s': float(np.percentile(timings, 50)),
            'p95_s': float(np.percentile(timings, 95))})
        output_arrays[f'feature_{i}'] = result
        print('IMAGE_RESULT=' + json.dumps(report['images'][-1]), flush=True)
    report_path = args.artifact_dir/f'check_{args.provider}.json'
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    np.savez_compressed(args.artifact_dir/f'features_{args.provider}.npz', **output_arrays)
    print(f'CHECK_REPORT={report_path}', flush=True)


if __name__ == '__main__':
    main()
