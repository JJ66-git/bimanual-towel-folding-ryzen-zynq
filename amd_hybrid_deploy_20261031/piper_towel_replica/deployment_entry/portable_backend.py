"""Portable device adapter. Checkpoint files and control parameters stay on disk unchanged."""
from pathlib import Path
import torch

BUNDLE = Path(__file__).resolve().parents[2]

def select_device(backend):
    if backend == 'cpu': return torch.device('cpu')
    if backend != 'rocm': raise ValueError('backend must be cpu or rocm')
    if not torch.version.hip or not torch.cuda.is_available():
        raise RuntimeError('ROCm requested but HIP-enabled PyTorch/device unavailable; run ./run.sh doctor --backend rocm')
    return torch.device('cuda')  # PyTorch HIP deliberately uses the CUDA namespace.

def seed_all(seed, device):
    torch.manual_seed(seed)
    if device.type == 'cuda': torch.cuda.manual_seed_all(seed)

def smol_config(model_path, device):
    from lerobot.policies.smolvla.configuration_smolvla import SmolVLAConfig
    config = SmolVLAConfig.from_pretrained(str(model_path))
    config.device = str(device)
    config.vlm_model_name = str(BUNDLE/'assets/smolvlm2_500m')
    return config

def place_policy(policy, device, precision):
    # 'original' keeps saved/runtime mixed dtypes; CPU auto expands them to FP32.
    if precision == 'float32' or (precision == 'auto' and device.type == 'cpu'):
        policy.to(device=device, dtype=torch.float32)
    else: policy.to(device)
    return policy.eval()
