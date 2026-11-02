#!/usr/bin/env python3
"""Inspect direct AI-PC dependencies and visible device nodes without controlling devices."""
import glob
import importlib.metadata
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT/'amd_hybrid_deploy_20261031'
SOURCE = PROJECT/'piper_towel_replica/runtime_source/src'
sys.path.insert(0, str(SOURCE))

def module(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False

def version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None

def can_identity(interface):
    try:
        device = (interface/'device').resolve(strict=True)
        for parent in (device, *device.parents):
            serial = parent/'serial'
            if serial.is_file():
                return {'device_path': str(device), 'usb_serial': serial.read_text().strip()}
        return {'device_path': str(device), 'usb_serial': None}
    except OSError as exc:
        return {'device_path': None, 'usb_serial': None, 'error': str(exc)}

def main():
    net = Path('/sys/class/net')
    report = {
        'scope': 'read-only software and visible-device inventory; not robot readiness or FPGA compute validation',
        'python': sys.executable,
        'packages': {name: version(name) for name in ('piper-sdk', 'python-can', 'opencv-python-headless', 'torch')},
        'modules_visible': {name: module(name) for name in ('piper_sdk', 'can', 'cv2', 'lerobot')},
        'lerobot_source': str(SOURCE),
        'can_interfaces': sorted(p.name for p in net.glob('can*')),
        'can_usb_identities': {p.name: can_identity(p) for p in sorted(net.glob('can*'))},
        'video_nodes': sorted(glob.glob('/dev/video*')),
        'usb_serial_by_id': sorted(glob.glob('/dev/serial/by-id/*')),
        'device_note': 'CAN and video entries may be absent or permission-limited until hardware is connected; USB serial alone does not prove FPGA acceleration.',
    }
    print('DIRECT_INVENTORY='+json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    for command in (['lsusb'], ['lsusb', '-t']):
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=10, check=False)
            print('USB_LIST (' + ' '.join(command) + '):', flush=True)
            print(result.stdout or result.stderr, end='', flush=True)
            if result.returncode:
                print(f'lsusb exited {result.returncode}', flush=True)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f'USB_LIST unavailable: {exc}', flush=True)

if __name__ == '__main__':
    main()
