"""Restore the image channel contract of the two fixed, already trained policies.

Real cameras and renderers keep returning RGB. Only policy inputs are adapted,
before their own saved normalizers, to match the observed training conversion.
"""
import hashlib
import json
from pathlib import Path

import torch

from lerobot.processor.normalize_processor import NormalizerProcessorStep
from lerobot.processor.pipeline import ObservationProcessorStep


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8*1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


class TrainingImageChannelStep(ObservationProcessorStep):
    def __init__(self, keys):
        self.keys = tuple(keys)

    def observation(self, observation):
        result = observation.copy()
        for key in self.keys:
            image = observation[key]
            if not isinstance(image, torch.Tensor) or image.ndim not in (4, 5) or image.shape[-3] != 3:
                raise ValueError(f'{key}: expected BCHW or BTCHW RGB tensor before normalization')
            # New tensors: preserve raw RGB arrays, sibling readers, state and metadata.
            result[key] = image.index_select(-3, torch.tensor([2, 1, 0], device=image.device))
        return result

    def transform_features(self, features):
        return features


def _insert_step(pipeline, expected_keys):
    if any(isinstance(step, TrainingImageChannelStep) for step in pipeline.steps):
        raise ValueError('Training image adapter already installed; refusing a double swap')
    normalizers = [(i, step) for i, step in enumerate(pipeline.steps)
        if isinstance(step, NormalizerProcessorStep)]
    if len(normalizers) != 1:
        raise ValueError('Expected exactly one saved policy normalizer')
    index, normalizer = normalizers[0]
    keys = [key for key, feature in normalizer.features.items()
        if getattr(feature.type, 'value', feature.type) == 'VISUAL']
    if keys != list(expected_keys):
        raise ValueError('Image feature ordering differs from the verified training image contract')
    step = TrainingImageChannelStep(keys)
    pipeline.steps = [*pipeline.steps[:index], step, *pipeline.steps[index:]]
    return keys


def install_training_image_adapter(pipeline, profile_path, model_path, policy_type):
    """Bind the correction to exact weights/config and install it once per pipeline."""
    profile = json.loads(Path(profile_path).read_text(encoding='utf-8'))
    if (profile.get('schema') != 'cobot_training_image_contract_v1'
            or profile.get('source_color_order') != 'RGB'
            or profile.get('policy_channel_permutation') != [2, 1, 0]):
        raise ValueError('Unsupported or ambiguous saved training image contract')
    expected = profile['policies'][policy_type]
    model_path = Path(model_path)
    for name, digest in expected['files'].items():
        if file_sha256(model_path/name) != digest:
            raise ValueError(f'Image contract is not verified for {policy_type}/{name}')
    keys = _insert_step(pipeline, expected['image_keys'])
    description = dict(policy=policy_type, source_color_order='RGB',
        policy_channel_permutation=[2, 1, 0], image_keys=keys,
        before_saved_normalizer=True, raw_images_unchanged=True,
        state_actions_stats_unchanged=True, profile_sha256=file_sha256(profile_path))
    pipeline.image_adaptation = description
    print('TRAINING_IMAGE_ADAPTER='+json.dumps(description), flush=True)
    return description


def restore_captured_image_adapter(pipeline, context):
    """Replay the captured contract, including old captures made before this fix."""
    description = context.get('image_adaptation')
    if description is None:
        return  # Historical captures intentionally reproduce their historical inputs.
    if (description.get('policy_channel_permutation') != [2, 1, 0]
            or description.get('before_saved_normalizer') is not True):
        raise ValueError('Unsupported captured training image adapter')
    _insert_step(pipeline, description['image_keys'])
    pipeline.image_adaptation = description.copy()
