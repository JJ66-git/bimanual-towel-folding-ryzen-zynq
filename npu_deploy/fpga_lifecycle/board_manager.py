#!/usr/bin/env python3
"""Persistent PYNQ display lifecycle manager, no robot or CAN access."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
from control_protocol import check_message, dumps, reply

ROOT = Path('/home/xilinx/jupyter_notebooks/fpga_offline_001/board_runtime')
KEY = ROOT / 'lifecycle' / 'control.key'
PEER = '192.168.2.10'
PORT = 9010
DELAY = 10.0


class Manager:
    def __init__(self, start_process, now=time.monotonic):
        self.start_process, self.now = start_process, now
        self.process = None
        self.deadline = None
        self.stopping = False

    def update(self):
        if self.process is not None and self.process.poll() is not None:
            print('LIFECYCLE_RUNTIME_EXIT code={}'.format(self.process.returncode), flush=True)
            self.process, self.deadline, self.stopping = None, None, False
        if self.process is not None and self.deadline is not None and self.now() >= self.deadline and not self.stopping:
            self.process.send_signal(signal.SIGINT)
            self.stopping = True
            self.deadline = None
            print('LIFECYCLE_STOP_SIGNAL sent', flush=True)

    def handle(self, operation):
        self.update()
        if operation == 'start':
            if self.stopping:
                return 'stopping'
            self.deadline = None
            if self.process is None:
                self.process = self.start_process()
                print('LIFECYCLE_RUNTIME_STARTED pid={}'.format(self.process.pid), flush=True)
            return 'starting_or_running'
        if operation == 'finish':
            if self.process is None:
                return 'already_stopped'
            if self.stopping:
                return 'stopping'
            self.deadline = self.now() + DELAY
            print('LIFECYCLE_STOP_SCHEDULED seconds=10', flush=True)
            return 'stop_scheduled_10s'
        if operation == 'status':
            if self.process is None: return 'stopped'
            if self.stopping: return 'stopping'
            if self.deadline is not None: return 'stop_scheduled_10s'
            return 'starting_or_running'
        raise ValueError('invalid operation')


def start_runtime():
    env = os.environ.copy()
    env.update(XILINX_XRT='/usr', BOARD='None', PYNQ_JUPYTER_NOTEBOOKS='/home/xilinx/jupyter_notebooks')
    env['PATH'] = '/usr/local/share/pynq-venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
    run_id = os.urandom(16).hex()
    return subprocess.Popen(['/bin/bash', str(ROOT/'scripts/run_fpga.sh'), PEER,
                             'host_observation', 'runs/live_'+run_id], cwd=str(ROOT), env=env)


def main():
    key = bytes.fromhex(KEY.read_text().strip())
    if len(key) != 32: raise RuntimeError('invalid control key')
    manager = Manager(start_runtime)
    seen = {}
    shutdown = False
    def stop(signum, frame):
        nonlocal shutdown
        shutdown = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(('0.0.0.0', PORT))
        sock.settimeout(0.25)
        print('LIFECYCLE_MANAGER_READY port={} peer={}'.format(PORT,PEER), flush=True)
        while not shutdown:
            manager.update()
            try: data, address = sock.recvfrom(2048)
            except socket.timeout: continue
            try:
                if address[0] != PEER or len(data)>1024: continue
                packet = check_message(key,json.loads(data.decode('ascii')),{'request'})
                if packet['value'] not in ('start','finish','status'): continue
                now = time.monotonic()
                seen = {nonce:t for nonce,t in seen.items() if now-t<120}
                if packet['nonce'] in seen or len(seen)>=1024: continue
                seen[packet['nonce']] = now
                state = manager.handle(packet['value'])
                sock.sendto(dumps(reply(key,packet,state)),address)
            except (UnicodeError, ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
                print('LIFECYCLE_REQUEST_ERROR {}'.format(type(exc).__name__),flush=True)
    if manager.process is not None and manager.process.poll() is None:
        manager.process.send_signal(signal.SIGINT)
        try: manager.process.wait(timeout=20)
        except subprocess.TimeoutExpired: print('LIFECYCLE_STOP_TIMEOUT',flush=True)


if __name__ == '__main__': main()
