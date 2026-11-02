#!/usr/bin/env python3
"""Experimental NPU-vision launcher, preserving the existing hybrid CLI."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT/'amd_hybrid_deploy_20261031'
PROJECT = BUNDLE/'piper_towel_replica'


def main():
    env = os.environ.copy()
    env.update(PYTHONPATH=':'.join(str(p) for p in [PROJECT/'runtime_source/src', PROJECT/'deployment_entry',
               PROJECT/'upstream/act_reference/src', BUNDLE/'shared_runtime']),
               TORCH_HOME=str(PROJECT/'deployment_assets/torch'), HF_HOME=str(BUNDLE/'cache/huggingface'),
               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONUNBUFFERED='1', PIPER_ACTION_FILTER_ALPHA='1.0')
    env.setdefault('OMP_NUM_THREADS', '4')
    env.setdefault('MKL_NUM_THREADS', '4')
    for key in ('LD_PRELOAD', 'LD_LIBRARY_PATH', 'GLIBC_TUNABLES', 'PYTHONHOME'):
        env.pop(key, None)
    original = PROJECT/'scripts/run_cobot.py'
    args = sys.argv[1:]
    command = [sys.executable, str(original), 'hybrid', '--backend', 'rocm', *args, '--check-only']
    result = subprocess.run(command, env=env, text=True, capture_output=True, check=True)
    launch = json.loads(result.stdout)
    index = launch.index('--entry') + 1
    if Path(launch[index]) != PROJECT/'deployment_entry/cobot_hybrid.py':
        raise RuntimeError('Unexpected original hybrid entry')
    launch[index] = str(ROOT/'npu_deploy/hybrid_entry.py')
    if '--check-only' in args:
        print(json.dumps(launch, ensure_ascii=False))
        return 0
    if sys.version_info[:2] != (3,12) or sys.platform != 'linux':
        raise RuntimeError('Requires Linux Python 3.12')
    sys.path.insert(0, str(PROJECT/'scripts'))
    from prepare_cobot import usb_serial
    site = json.loads((PROJECT/'configs/cobot_site.json').read_text())
    for side in ('left', 'right'):
        port = site['robot'][side+'_port']
        if usb_serial((Path('/sys/class/net')/port/'device').resolve(strict=True)) != site['usb_serials'][side]:
            raise ValueError('CAN USB identity mismatch; run ./run.sh prepare --check-only')
    os.chdir(PROJECT)
    print('PORTABLE_COBOT: hybrid NPU vision', 'EXECUTE' if '--execute' in args else 'READ-ONLY', flush=True)
    os.execve(sys.executable, launch, env)


if __name__ == '__main__':
    raise SystemExit(main())
