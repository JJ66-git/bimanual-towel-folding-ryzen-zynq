#!/usr/bin/env python3
"""Run and verify a small real ROCm matrix multiplication."""
import time
import torch

torch.set_num_threads(4)
if not torch.version.hip:
    raise RuntimeError('This PyTorch build is not a ROCm/HIP build')
if not torch.cuda.is_available():
    raise RuntimeError('ROCm GPU is not available')
device = torch.device('cuda:0')  # PyTorch uses the cuda namespace for ROCm too.
props = torch.cuda.get_device_properties(device)
print('PyTorch:', torch.__version__)
print('ROCm/HIP:', torch.version.hip)
print('GPU:', props.name)
print('GPU architecture:', getattr(props, 'gcnArchName', 'not reported'))
torch.manual_seed(42)
a = torch.randn((256, 256), device=device, dtype=torch.float32)
b = torch.randn((256, 256), device=device, dtype=torch.float32)
torch.cuda.synchronize()
start = time.perf_counter()
result = a @ b
torch.cuda.synchronize()
elapsed = time.perf_counter() - start
cpu_reference = a.cpu() @ b.cpu()
result_cpu = result.cpu()
max_error = (result_cpu - cpu_reference).abs().max().item()
if not torch.isfinite(result_cpu).all().item() or not torch.allclose(result_cpu, cpu_reference, rtol=0.003, atol=0.001):
    raise RuntimeError(f'GPU result differs from CPU reference: max error {max_error}')
print('Operation:', '256x256 float32 matrix multiplication')
print('Output device:', result.device)
print('Elapsed (s):', f'{elapsed:.6f}')
print('CPU comparison:', 'passed', 'max_abs_error=', f'{max_error:.8g}')
