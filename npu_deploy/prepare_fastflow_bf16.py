#!/usr/bin/env python3
"""Create a separate BF16 FastFlow candidate from the trained checkpoint."""
import hashlib
import json
from pathlib import Path

import torch
from safetensors import safe_open
from safetensors.torch import save_file

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'amd_hybrid_deploy_20261031/piper_towel_replica/models/smol030000/model.safetensors'
REFERENCE = ROOT/'npu_deploy/vendor/FLM_VLA/FLM_SmolVLA/model.safetensors'
PREPARED = ROOT/'npu_deploy/artifacts/fastflow_custom'
OUT = ROOT/'npu_deploy/artifacts/fastflow_custom_bf16'


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    torch.set_num_threads(4)
    OUT.mkdir(parents=True, exist_ok=True)
    original_sha = digest(SOURCE)
    prep = json.loads((PREPARED/'provenance.json').read_text())
    if prep['trained_weights_sha256'] != original_sha:
        raise ValueError('Prepared configuration does not match trained weights')
    tensors, converted = {}, []
    with safe_open(SOURCE, framework='pt', device='cpu') as a, safe_open(REFERENCE, framework='pt', device='cpu') as b:
        if set(a.keys()) != set(b.keys()):
            raise ValueError('Checkpoint tensor names differ')
        for name in a.keys():
            target = b.get_slice(name)
            tensor = a.get_tensor(name)
            if tensor.shape != torch.Size(target.get_shape()) or target.get_dtype() != 'BF16':
                raise ValueError(f'Unsupported tensor shape or target dtype: {name}')
            if tensor.dtype == torch.float32:
                rounded = tensor.to(torch.bfloat16)
                delta = (rounded.float() - tensor).abs()
                converted.append({'name': name, 'max_abs_rounding_error': float(delta.max())})
                tensor = rounded
            elif tensor.dtype != torch.bfloat16:
                raise ValueError(f'Unsupported source dtype: {name}: {tensor.dtype}')
            tensors[name] = tensor.contiguous()
    out_weights = OUT/'model.safetensors'
    save_file(tensors, str(out_weights))
    (OUT/'config.json').write_bytes((PREPARED/'config.json').read_bytes())
    report = {'source_sha256': original_sha, 'candidate_sha256': digest(out_weights),
              'tensor_count': len(tensors), 'converted_from_float32': converted,
              'note': 'Independent BF16 copy for FastFlow NPU compatibility; changes precision and needs output comparison.'}
    (OUT/'provenance.json').write_text(json.dumps(report, indent=2)+'\n')
    print(f'BF16_MODEL={OUT} tensors={len(tensors)} converted={len(converted)}', flush=True)
    print(f'BF16_SHA256={report["candidate_sha256"]}', flush=True)


if __name__ == '__main__':
    main()
