#!/usr/bin/env python3
"""Private offline ONNX encoder worker; communicates over inherited pipes."""
import argparse
import io
import os
import struct
import traceback
from pathlib import Path

import numpy as np
import onnxruntime as ort


def receive(fd):
    header = read_exact(fd, 4)
    size = struct.unpack('!I', header)[0]
    if size > 64 * 1024 * 1024:
        raise ValueError('Request exceeds encoder protocol limit')
    return read_exact(fd, size)


def read_exact(fd, size):
    chunks = []
    while size:
        chunk = os.read(fd, size)
        if not chunk:
            raise EOFError('Encoder socket closed')
        chunks.append(chunk)
        size -= len(chunk)
    return b''.join(chunks)


def send(fd, data):
    data = memoryview(struct.pack('!I', len(data)) + data)
    while data:
        data = data[os.write(fd, data):]


def main():
    # XDNA was preloaded by the dynamic loader before Python started. Prevent
    # compiler subprocesses from preloading it under a different library path.
    os.environ.pop('LD_PRELOAD', None)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-fd', type=int, required=True)
    parser.add_argument('--output-fd', type=int, required=True)
    parser.add_argument('--provider', choices=['cpu', 'npu'], required=True)
    parser.add_argument('--model', type=Path, required=True)
    args = parser.parse_args()
    input_fd, output_fd = args.input_fd, args.output_fd
    try:
        providers = ['VitisAIExecutionProvider', 'CPUExecutionProvider'] if args.provider == 'npu' else ['CPUExecutionProvider']
        if any(p not in ort.get_available_providers() for p in providers):
            raise RuntimeError(f'Provider unavailable: {providers}')
        options = ort.SessionOptions()
        options.log_severity_level = 1 if args.provider == 'npu' else 2
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(str(args.model), sess_options=options, providers=providers)
        if args.provider == 'npu' and 'VitisAIExecutionProvider' not in session.get_providers():
            raise RuntimeError('NPU provider is not registered')
        send(output_fd, b'R')
        while True:
            data = receive(input_fd)
            if data == b'Q':
                return
            pixels = np.load(io.BytesIO(data), allow_pickle=False)
            if pixels.shape != (1, 3, 512, 512) or pixels.dtype != np.float32:
                raise ValueError(f'Unexpected encoder input: {pixels.shape}, {pixels.dtype}')
            result = session.run(['image_features'], {'pixel_values': pixels})[0]
            if result.shape != (1, 64, 960) or not np.isfinite(result).all():
                raise RuntimeError('Unexpected encoder result')
            output = io.BytesIO()
            np.save(output, result, allow_pickle=False)
            send(output_fd, b'O' + output.getvalue())
    except EOFError:
        return
    except Exception:
        error = traceback.format_exc()
        try:
            send(output_fd, b'E' + error.encode())
        finally:
            raise


if __name__ == '__main__':
    main()
