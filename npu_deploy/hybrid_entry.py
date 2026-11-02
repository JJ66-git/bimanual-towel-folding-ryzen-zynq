#!/usr/bin/env python3
"""Experimental hybrid controller entry using an NPU SmolVLA image encoder."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT/'amd_hybrid_deploy_20261031'
DEPLOY = BUNDLE/'piper_towel_replica/deployment_entry'
sys.path[:0] = [str(Path(__file__).parent), str(DEPLOY)]

from vision_client import VisionClient
import cobot_hybrid


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class NpuSmolLoader:
    original = cobot_hybrid.SmolVLAPolicy
    clients = []

    @classmethod
    def from_pretrained(cls, checkpoint, *args, **kwargs):
        checkpoint = Path(checkpoint)
        artifacts = ROOT/'npu_deploy/artifacts/smol_vision'
        report = json.loads((artifacts/'export.json').read_text())
        for path, key in ((checkpoint/'model.safetensors', 'weights_sha256'),
                          (checkpoint/'config.json', 'config_sha256'),
                          (artifacts/'smol_vision.onnx', 'onnx_sha256')):
            if digest(path) != report[key]:
                raise ValueError(f'NPU encoder artifact does not match SmolVLA: {path}')
        policy = cls.original.from_pretrained(str(checkpoint), *args, **kwargs)
        vision = policy.model.vlm_with_expert
        encoder = vision.get_vlm_model().vision_model
        client = VisionClient('npu', artifacts/'smol_vision.onnx', next(encoder.parameters()).dtype)
        cls.clients.append(client)

        def embed_image(image):
            # place_policy can change dtype after this model is loaded.
            client.dtype = next(encoder.parameters()).dtype
            return client(image)

        vision.embed_image = embed_image
        print('SMOL_VISION_BACKEND=NPU (runtime reported an NPU subgraph; inference pending)', flush=True)
        return policy


def main():
    cobot_hybrid.SmolVLAPolicy = NpuSmolLoader
    try:
        return cobot_hybrid.main()
    finally:
        for client in NpuSmolLoader.clients:
            client.close()


if __name__ == '__main__':
    raise SystemExit(main())
