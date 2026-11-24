"""Inference-only vision optimizations for the deployed ACT ResNet-18."""

from torch import nn, no_grad
from torch.nn.utils import fuse_conv_bn_eval
from torchvision.models._utils import IntermediateLayerGetter
from torchvision.models.resnet import BasicBlock
from torchvision.ops.misc import FrozenBatchNorm2d


@no_grad()
def optimize_act_refill_backbone(model) -> bool:
    """Fuse frozen norms and enable batched camera encoding."""
    if model.training or model.config.vision_backbone != "resnet18" or not model.config.image_features:
        return False

    pairs = []
    for parent in model.backbone.modules():
        if not isinstance(parent, (IntermediateLayerGetter, BasicBlock, nn.Sequential)):
            continue
        children = list(parent.named_children())
        for (conv_name, conv), (norm_name, norm) in zip(children, children[1:]):
            if isinstance(conv, nn.Conv2d) and isinstance(norm, FrozenBatchNorm2d):
                pairs.append((parent, conv_name, conv, norm_name, norm))

    # The deployed backbone has one stem, sixteen block, and three residual projections.
    if len(pairs) != 20:
        return False
    fused = [
        (parent, conv_name, fuse_conv_bn_eval(conv, norm), norm_name)
        for parent, conv_name, conv, norm_name, norm in pairs
    ]
    for parent, conv_name, conv, norm_name in fused:
        parent.add_module(conv_name, conv)
        parent.add_module(norm_name, nn.Identity())
    model.batch_camera_backbone = True
    return True
