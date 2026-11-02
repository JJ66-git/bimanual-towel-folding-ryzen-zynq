"""Offline subprocess adapter for the independently installed Ryzen AI runtime."""
import io
import os
import select
import struct
import subprocess
import threading
import time
import re
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT/'ryzen-ai-npu/venv'
SITE = SDK/'lib/python3.12/site-packages'


def read_exact(fd, length, timeout):
    parts = []
    deadline = time.monotonic() + timeout
    while length:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise TimeoutError('Timed out waiting for vision worker')
        part = os.read(fd, length)
        if not part:
            raise EOFError('Vision worker stopped unexpectedly')
        parts.append(part)
        length -= len(part)
    return b''.join(parts)


def recv(fd, timeout):
    length = struct.unpack('!I', read_exact(fd, 4, timeout))[0]
    if length > 64 * 1024 * 1024:
        raise RuntimeError('Vision worker response exceeds protocol limit')
    return read_exact(fd, length, timeout)


def send(fd, data):
    data = memoryview(struct.pack('!I', len(data)) + data)
    while data:
        data = data[os.write(fd, data):]


class NpuLogMonitor:
    """Forward native runtime stderr and record a positive NPU partition report."""
    def __init__(self, pipe, sink_fd=2):
        self.pipe = pipe
        self.sink_fd = sink_fd
        self.partition_seen = threading.Event()
        self.finished = threading.Event()
        self.thread = threading.Thread(target=self._run, name='npu-runtime-log', daemon=True)
        self.thread.start()

    def _run(self):
        try:
            while True:
                line = self.pipe.readline()
                if not line:
                    break
                if re.search(rb'Actually running on NPU\s+[1-9][0-9]*', line):
                    self.partition_seen.set()
                try:
                    os.write(self.sink_fd, line)
                except OSError:
                    pass
        finally:
            self.finished.set()


class VisionClient:
    def __init__(self, provider, model, dtype, timeout=600):
        if provider not in ('cpu', 'npu'):
            raise ValueError('Unknown worker provider')
        self.provider = provider
        self._first_result_reported = False
        worker_read, parent_write = os.pipe()
        parent_read, worker_write = os.pipe()
        env = os.environ.copy()
        for key in ('PYTHONPATH', 'PYTHONHOME', 'LD_PRELOAD', 'GLIBC_TUNABLES'):
            env.pop(key, None)
        libs = ['/opt/xilinx/xrt/lib', str(ROOT/'amd_hybrid_deploy_20261031/.tooling/python/cpython-3.12.16-linux-x86_64-gnu/lib'),
                '/lib/x86_64-linux-gnu', str(SDK/'onnxruntime/lib'),
                str(SITE/'flexml/flexml_extras/lib'), str(SITE/'onnxruntime/capi'), str(SITE/'voe/lib')]
        env.update(RYZEN_AI_INSTALLATION_PATH=str(SDK), XILINX_XRT='/opt/xilinx/xrt',
                   XILINX_VITIS=str(SITE), XILINX_VITIS_AIETOOLS=str(SITE),
                   LD_LIBRARY_PATH=':'.join(libs), GLIBC_TUNABLES='glibc.rtld.execstack=2')
        if provider == 'npu':
            env['LD_PRELOAD'] = '/opt/xilinx/xrt/lib/libxrt_driver_xdna.so.2'
        cmd = [str(SDK/'bin/python'), str(ROOT/'npu_deploy/vision_worker.py'),
               '--input-fd', str(worker_read), '--output-fd', str(worker_write),
               '--provider', provider, '--model', str(model)]
        self.read_fd, self.write_fd, self.dtype, self.timeout = parent_read, parent_write, dtype, timeout
        try:
            self.process = subprocess.Popen(cmd, env=env, pass_fds=(worker_read, worker_write),
                                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            self.log_monitor = NpuLogMonitor(self.process.stderr)
        except BaseException:
            for fd in (parent_read, parent_write, worker_read, worker_write):
                os.close(fd)
            raise
        os.close(worker_read)
        os.close(worker_write)
        try:
            ready = recv(parent_read, timeout)
            if ready != b'R':
                raise RuntimeError(ready[1:].decode(errors='replace'))
            if provider == 'npu' and not self.log_monitor.partition_seen.wait(5):
                raise RuntimeError('NPU runtime did not report an assigned NPU subgraph; '
                                   'the session may have fallen back to CPU. Inspect runtime logs.')
            if provider == 'npu':
                print('NPU_PARTITION_CONFIRMED: runtime reported at least one NPU subgraph', flush=True)
        except BaseException:
            self.close()
            raise

    def __call__(self, image):
        array = image.detach().to(device='cpu', dtype=torch.float32).contiguous().numpy()
        buffer = io.BytesIO()
        np.save(buffer, array, allow_pickle=False)
        send(self.write_fd, buffer.getvalue())
        data = recv(self.read_fd, self.timeout)
        if data[:1] != b'O':
            raise RuntimeError(data[1:].decode(errors='replace'))
        result = np.load(io.BytesIO(data[1:]), allow_pickle=False)
        if self.provider == 'npu' and not self._first_result_reported:
            print('NPU_FIRST_INFERENCE_COMPLETED: output_shape='+str(result.shape), flush=True)
            self._first_result_reported = True
        return torch.from_numpy(result).to(device=image.device, dtype=self.dtype)

    def close(self):
        if self.write_fd is not None:
            try:
                send(self.write_fd, b'Q')
            except OSError:
                pass
            os.close(self.write_fd)
            os.close(self.read_fd)
            self.write_fd = None
        if getattr(self, 'process', None) is not None:
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=10)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
