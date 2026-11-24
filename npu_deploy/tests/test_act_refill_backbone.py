from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

import torch
from torchvision.models import resnet18
from torchvision.models._utils import IntermediateLayerGetter
from torchvision.ops.misc import FrozenBatchNorm2d

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "amd_hybrid_deploy_20261031/piper_towel_replica/deployment_entry"))
from act_refill_backbone import optimize_act_refill_backbone


class ActRefillBackboneTest(unittest.TestCase):
    def setUp(self):
        backbone = resnet18(weights=None, norm_layer=FrozenBatchNorm2d)
        self.model = torch.nn.Module()
        self.model.config = SimpleNamespace(vision_backbone="resnet18", image_features={"camera": None})
        self.model.backbone = IntermediateLayerGetter(backbone, return_layers={"layer4": "feature_map"})

    def test_eval_fuses_all_frozen_norms_and_preserves_feature_map(self):
        self.model.eval()
        with torch.no_grad():
            for norm in self.model.backbone.modules():
                if isinstance(norm, FrozenBatchNorm2d):
                    norm.weight.uniform_(0.5, 1.5)
                    norm.bias.uniform_(-0.2, 0.2)
                    norm.running_mean.uniform_(-0.2, 0.2)
                    norm.running_var.uniform_(0.5, 1.5)
            image = torch.rand(1, 3, 48, 64)
            reference = self.model.backbone(image)["feature_map"]
            self.assertTrue(optimize_act_refill_backbone(self.model))
            actual = self.model.backbone(image)["feature_map"]
        torch.testing.assert_close(actual, reference, rtol=1e-4, atol=3e-5)
        self.assertFalse(any(isinstance(norm, FrozenBatchNorm2d) for norm in self.model.backbone.modules()))
        self.assertTrue(self.model.batch_camera_backbone)
        self.assertFalse(optimize_act_refill_backbone(self.model))

    def test_training_or_incomplete_topology_stays_native(self):
        self.assertFalse(optimize_act_refill_backbone(self.model))
        self.model.eval()
        stem = self.model.backbone.conv1
        self.model.backbone.layer4[1].bn2 = torch.nn.Identity()
        self.assertFalse(optimize_act_refill_backbone(self.model))
        self.assertIs(self.model.backbone.conv1, stem)
        self.assertIsInstance(self.model.backbone.bn1, FrozenBatchNorm2d)


if __name__ == "__main__":
    unittest.main()
