#!/usr/bin/env python3
"""Verify that the demo's exported encoder belongs to this trained checkpoint."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
bundle = root / 'amd_hybrid_deploy_20261031/piper_towel_replica/models/smol030000'
artifacts = root / 'npu_deploy/artifacts/smol_vision'
metadata = json.loads((artifacts / 'export.json').read_text())
for path, key in ((bundle/'model.safetensors', 'weights_sha256'),
                  (bundle/'config.json', 'config_sha256'),
                  (artifacts/'smol_vision.onnx', 'onnx_sha256')):
    with path.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    print(f'{path.name}: {actual}', flush=True)
    if actual != metadata[key]:
        raise RuntimeError(f'Export/checkpoint mismatch: {path}')
print('Checkpoint and ONNX match recorded export metadata.')
