from collections import deque
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'amd_hybrid_deploy_20261031/piper_towel_replica/runtime_source/src'))
from lerobot.rollout.inference.sync import SyncInferenceEngine


class CachedACTTest(unittest.TestCase):
    def test_queued_actions_skip_rgb_preprocessing_until_task_change(self):
        queue = deque()

        def select_action(observation):
            if not queue:
                self.assertIn('task', observation)
                value = 1. if observation['task'] == 'fold' else 10.
                queue.extend((torch.tensor([[value, 0.]]), torch.tensor([[value + 1., 0.]])))
            else:
                self.assertEqual(observation, {})
            return queue.popleft()

        act = SimpleNamespace(config=SimpleNamespace(use_amp=False),
                              select_action=select_action, drop_queued_actions=queue.clear)
        pre = Mock(side_effect=lambda observation: observation)
        engine = SyncInferenceEngine(
            policy=act, preprocessor=pre, postprocessor=lambda action: action,
            dataset_features={'action': {'names': ['joint', 'gripper']}},
            ordered_action_keys=['joint', 'gripper'],
            task='fold', device='cpu', robot_type='piper_dual', queued_action_ready=lambda: bool(queue),
        )
        observation = {'observation.state': np.zeros(1, dtype=np.float32)}
        self.assertEqual(engine.get_action(observation).tolist(), [1., 0.])
        self.assertEqual(engine.get_action(observation).tolist(), [2., 0.])
        self.assertEqual(pre.call_count, 1)
        self.assertEqual(engine.get_action(observation).tolist(), [1., 0.])
        self.assertTrue(engine.set_task('return'))
        self.assertEqual(engine.get_action(observation).tolist(), [10., 0.])
        self.assertEqual(pre.call_count, 3)


if __name__ == '__main__':
    unittest.main()
