#!/usr/bin/env python3
"""Prepare an isolated FastFlowLM model directory pointing to our trained weights."""
import hashlib
import json
import os
from pathlib import Path

from safetensors import safe_open

ROOT = Path(__file__).resolve().parents[1]
OWN = ROOT/'amd_hybrid_deploy_20261031/piper_towel_replica/models/smol030000'
VENDOR = ROOT/'npu_deploy/vendor/FLM_VLA/FLM_SmolVLA'
OUT = ROOT/'npu_deploy/artifacts/fastflow_custom'


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    own, vendor = OWN/'model.safetensors', VENDOR/'model.safetensors'
    with safe_open(own, framework='pt', device='cpu') as a, safe_open(vendor, framework='pt', device='cpu') as b:
        own_keys, vendor_keys = set(a.keys()), set(b.keys())
        if own_keys != vendor_keys:
            raise ValueError(f'Checkpoint keys differ: own-only={sorted(own_keys-vendor_keys)}, vendor-only={sorted(vendor_keys-own_keys)}')
        differences = [k for k in own_keys if a.get_slice(k).get_shape() != b.get_slice(k).get_shape()]
        if differences:
            raise ValueError(f'Checkpoint shapes differ: {differences}')
    own_config = json.loads((OWN/'config.json').read_text())
    vendor_config = json.loads((VENDOR/'config.json').read_text())
    # These define the compiled network. Robot state/action widths intentionally
    # remain those of our trained model; the network pads them internally.
    architecture = ('type', 'chunk_size', 'n_action_steps', 'max_state_dim', 'max_action_dim',
                    'resize_imgs_with_padding', 'tokenizer_max_length', 'num_steps', 'attention_mode',
                    'num_vlm_layers', 'num_expert_layers', 'self_attn_every_n_layers', 'expert_width_multiplier')
    differences = {k: [own_config.get(k), vendor_config.get(k)] for k in architecture
                   if own_config.get(k) != vendor_config.get(k)}
    if differences:
        raise ValueError(f'FastFlow architecture config differs: {differences}')
    for key in ('vision_config', 'expert_config'):
        if key in own_config and own_config[key] != vendor_config[key]:
            raise ValueError(f'Conflicting {key}')
        own_config[key] = vendor_config[key]
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT/'model.safetensors'
    if target.is_symlink():
        if target.resolve() != own.resolve():
            raise ValueError(f'Unexpected existing model link: {target}')
    elif target.exists():
        raise ValueError(f'Refusing to replace existing model file: {target}')
    else:
        target.symlink_to(os.path.relpath(own, OUT))
    (OUT/'config.json').write_text(json.dumps(own_config, indent=2) + '\n')
    report = {'trained_weights_sha256': digest(own), 'original_config_sha256': digest(OWN/'config.json'),
              'fastflow_reference_weights_sha256': digest(vendor), 'matched_tensor_count': len(own_keys),
              'model_path': str(OUT), 'added_config_fields': ['vision_config','expert_config'],
              'note': 'Experimental full plain inference, not yet verified on NPU; guided RTC remains separate.'}
    (OUT/'provenance.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
