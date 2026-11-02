#!/usr/bin/env python3
"""Export the trained SmolVLA image encoder and reference fixture, without hardware."""
import argparse
import copy
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / 'amd_hybrid_deploy_20261031'
POLICY = BUNDLE / 'piper_towel_replica'
SDK_SITE = ROOT / 'ryzen-ai-npu/venv/lib/python3.12/site-packages'
sys.path[:0] = [str(POLICY / 'runtime_source/src'), str(POLICY / 'deployment_entry')]
# ONNX is already installed in the separate Ryzen AI environment. Append rather
# than prepend it so that the project's Torch, NumPy and other pins take priority.
sys.path.append(str(SDK_SITE))
os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                  TORCH_HOME=str(POLICY / 'deployment_assets/torch'))

import numpy as np
import torch
from PIL import Image
from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy
from lerobot.policies.factory import make_pre_post_processors
from lerobot.policies.utils import prepare_observation_for_inference
from portable_backend import smol_config, place_policy
from training_effort_adapter import STATE_NAMES, install_training_effort_adapter
from training_image_adapter import install_training_image_adapter


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


class VisionEncoder(torch.nn.Module):
    def __init__(self, vlm):
        super().__init__()
        self.vision = vlm.vision_model
        self.connector = vlm.connector
        # prepare_images always supplies a full 512x512 image, and embed_image
        # uses an all-true patch mask. Precompute exactly the same position IDs
        # to avoid exporting bucketize/masked assignment into ONNX.
        emb = self.vision.embeddings
        grid = 512 // self.vision.patch_size
        boundaries = torch.arange(1 / emb.num_patches_per_side, 1.0, 1 / emb.num_patches_per_side)
        coords = torch.clamp(torch.arange(grid, dtype=torch.float32) / grid, max=1.0 - 1e-6)
        buckets = torch.bucketize(coords, boundaries, right=True)
        ids = (buckets[:, None] * emb.num_patches_per_side + buckets[None, :]).reshape(1, -1)
        self.register_buffer('position_ids', ids)

    def forward(self, pixels):
        # With the model's all-true patch mask, attention is unmasked.
        emb = self.vision.embeddings
        x = emb.patch_embedding(pixels).flatten(2).transpose(1, 2)
        x = x + emb.position_embedding(self.position_ids)
        x = self.vision.encoder(inputs_embeds=x, attention_mask=None).last_hidden_state
        x = self.vision.post_layernorm(x)
        return self.connector(x)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, default=ROOT / 'npu_deploy/artifacts/smol_vision')
    p.add_argument('--threads', type=int, default=4)
    args = p.parse_args()
    if args.threads < 1:
        p.error('--threads must be positive')
    if not SDK_SITE.is_dir():
        p.error(f'Ryzen AI ONNX package directory not found: {SDK_SITE}')
    import onnx
    args.out.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(args.threads)
    device = torch.device('cpu')
    checkpoint = POLICY / 'models/smol030000'
    cfg = smol_config(checkpoint, device)
    policy = SmolVLAPolicy.from_pretrained(str(checkpoint), config=cfg, strict=True)
    place_policy(policy, device, 'float32')
    pre, _ = make_pre_post_processors(policy.config, str(checkpoint),
        preprocessor_overrides={'device_processor': {'device': 'cpu'}})
    install_training_image_adapter(pre, POLICY/'configs/cobot_observation.json', checkpoint, 'smolvla')
    install_training_effort_adapter(pre, STATE_NAMES, 'smolvla')
    fixture = json.loads((BUNDLE/'fixtures/observation.json').read_text())
    values = dict(zip(fixture['feature_names'], fixture['state'], strict=True))
    if set(values) != set(STATE_NAMES):
        raise ValueError('Fixture does not match the model state contract')
    frame = {'observation.state': np.asarray([values[k] for k in STATE_NAMES], dtype=np.float32)}
    for role, name in fixture['images'].items():
        frame['observation.images.' + role] = np.asarray(Image.open(BUNDLE/'fixtures'/name).convert('RGB'))
    batch = pre(prepare_observation_for_inference(copy.deepcopy(frame), device, fixture['task'], 'piper_dual'))
    images, masks = policy.prepare_images(batch)
    if len(images) != 3 or any(not bool(m.all()) for m in masks):
        raise ValueError('Expected three present images in fixture')
    model = VisionEncoder(policy.model.vlm_with_expert.get_vlm_model()).eval()
    with torch.inference_mode():
        expected = [policy.model.vlm_with_expert.embed_image(image).cpu().numpy() for image in images]
        adapted = [model(image).cpu().numpy() for image in images]
    changes = [float(np.max(np.abs(a-b))) for a,b in zip(expected, adapted, strict=True)]
    for reference, candidate in zip(expected, adapted, strict=True):
        np.testing.assert_allclose(candidate, reference, rtol=1e-4, atol=1e-4)
    if any(not np.isfinite(x).all() for x in expected):
        raise ValueError('Non-finite PyTorch image features')
    np.savez_compressed(args.out/'fixture.npz', **{f'image_{i}': x.cpu().numpy() for i,x in enumerate(images)},
                        **{f'feature_{i}': x for i,x in enumerate(expected)})
    onnx_path = args.out/'smol_vision.onnx'
    # The legacy exporter is used because this environment has no onnxscript.
    torch.onnx.export(model, (images[0],), str(onnx_path), input_names=['pixel_values'],
                      output_names=['image_features'], opset_version=17, dynamo=False)
    graph = onnx.load(str(onnx_path))
    onnx.checker.check_model(graph)
    report = {'checkpoint': str(checkpoint), 'weights_sha256': digest(checkpoint/'model.safetensors'),
              'config_sha256': digest(checkpoint/'config.json'), 'onnx_sha256': digest(onnx_path),
              'input_shape': list(images[0].shape), 'feature_shape': list(expected[0].shape),
              'export_wrapper_max_abs_vs_native': changes,
              'fixture_images': 3, 'torch': torch.__version__, 'onnx': onnx.__version__,
              'precision': 'float32', 'opset': 17,
              'note': 'Full trained checkpoint; the real image adapter and preprocessing; CPU reference only.'}
    (args.out/'export.json').write_text(json.dumps(report, indent=2) + '\n')
    print('EXPORT_REPORT=' + json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
