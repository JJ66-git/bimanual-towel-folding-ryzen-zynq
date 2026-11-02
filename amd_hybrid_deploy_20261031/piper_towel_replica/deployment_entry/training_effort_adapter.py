"""Match constant-effort training inputs without changing telemetry or normalization.

Machine/data adaptation at the reference LeRobot processor boundary. Checkpoint,
SDK feedback, positions, images, variable efforts and action processing stay intact.
"""
import json
import numpy as np
import torch

from lerobot.configs.types import NormalizationMode
from lerobot.processor.normalize_processor import NormalizerProcessorStep
from lerobot.processor.pipeline import ObservationProcessorStep

STATE_NAMES = [f'{side}_{motor}.{kind}' for side in ('left','right')
               for motor in [*(f'joint_{i}' for i in range(1,7)), 'gripper']
               for kind in ('pos','effort')]


class TrainingConstantEffortStep(ObservationProcessorStep):
    def __init__(self, indices, values, names):
        self.indices = tuple(indices)
        self.values = tuple(values)
        self.names = tuple(names)
        if any(not names[i].endswith('.effort') for i in indices):
            raise ValueError('Adapter must not replace positions')

    def observation(self, observation):
        state = observation['observation.state']
        if state.shape[-1] != 28:
            raise ValueError('Constant effort adapter requires original 28-state, before model padding')
        if isinstance(state, torch.Tensor):
            result = state.clone()
            result[..., list(self.indices)] = torch.as_tensor(self.values, device=state.device, dtype=state.dtype)
        else:
            result = np.array(state, copy=True)
            result[..., list(self.indices)] = self.values
        return {**observation, 'observation.state': result}

    def transform_features(self, features):
        return features

    def description(self):
        return [dict(index=i, name=self.names[i], training_constant=float(v))
                for i,v in zip(self.indices, self.values, strict=True)]


def install_training_effort_adapter(pipeline, names, label):
    if list(names) != STATE_NAMES:
        raise ValueError('Expected reference interleaved pos/effort feature ordering')
    if any(isinstance(s, TrainingConstantEffortStep) for s in pipeline.steps):
        raise ValueError('Effort adapter already installed')
    normalizers = [(i,s) for i,s in enumerate(pipeline.steps) if isinstance(s, NormalizerProcessorStep)]
    if len(normalizers) != 1:
        raise ValueError('Expected one saved normalizer in reference processor')
    index, normalizer = normalizers[0]
    feature = normalizer.features['observation.state']
    mode = normalizer.norm_map[feature.type]
    report = dict(policy=label, mode=getattr(mode,'value',str(mode)), constant_efforts=[], positions_unchanged=True,
                  raw_sdk_feedback_unchanged=True, checkpoint_stats_unchanged=True)
    if mode != NormalizationMode.MEAN_STD:
        report['note'] = 'Not MEAN_STD; no std-based adaptation'
        print('TRAINING_EFFORT_ADAPTER='+json.dumps(report), flush=True)
        return report
    flat = normalizer.state_dict()
    arrays = {k.removeprefix('observation.state.'):v.detach().float().cpu().numpy().reshape(-1)
              for k,v in flat.items() if k.startswith('observation.state.')}
    mean, std = arrays['mean'], arrays['std']
    if mean.shape != (28,) or std.shape != (28,) or not np.isfinite(mean).all() or not np.isfinite(std).all() or (std<0).any():
        raise ValueError('Saved mean/std not a valid 28-state contract')
    indices = [i for i,n in enumerate(names) if n.endswith('.effort') and std[i] == 0]
    for key in ('min','max'):
        if key in arrays:
            if arrays[key].shape != (28,):
                raise ValueError('Saved '+key+' has inconsistent shape')
            if any(arrays[key][i] != mean[i] for i in indices):
                raise ValueError('Saved effort min/max contradict zero variance; do not mask potentially variable data')
    if indices:
        adapter = TrainingConstantEffortStep(indices, [float(mean[i]) for i in indices], names)
        pipeline.steps = [*pipeline.steps[:index], adapter, *pipeline.steps[index:]]
        report['constant_efforts'] = adapter.description()
    report['unmodified_constant_positions'] = [n for i,n in enumerate(names) if n.endswith('.pos') and std[i] == 0]
    print('TRAINING_EFFORT_ADAPTER='+json.dumps(report), flush=True)
    return report
