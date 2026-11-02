#!/usr/bin/env python3
"""Control diagnostic display lifecycle, never robot motion."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from control_protocol import check_message, dumps, make_message

KEY = ROOT/'artifacts/fpga_lifecycle/control.key'


def request(op, host='192.168.2.99', port=9010, timeout=2):
    key = bytes.fromhex(KEY.read_text().strip())
    if len(key) != 32: raise RuntimeError('FPGA lifecycle key invalid')
    packet = make_message(key,'request',op)
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.connect((host,port))
        sock.send(dumps(packet))
        response=check_message(key,json.loads(sock.recv(2048).decode('ascii')),{'reply'})
    if response['nonce'] != packet['nonce']: raise RuntimeError('FPGA lifecycle nonce mismatch')
    return response['value']


def wait_for_service(host, port, timeout=45):
    for base in (ROOT.parent/'fpga_integration/fpga_code/fpga_workspace',ROOT.parent/'fpga'):
        if (base/'host/task_status.py').is_file():
            sys.path.insert(0,str(base)); break
    else: raise RuntimeError('FPGA protocol code missing')
    from host.task_status import Client
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        client=Client((host,port),timeout=0.7)
        try:
            client.send('WAITING','IDLE','HOST')
            return
        except (TimeoutError, OSError):
            time.sleep(1)
        finally:
            client.close()
    raise TimeoutError('FPGA runtime not ready within 45 s')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=('start','finish','status'))
    parser.add_argument('--host',default='192.168.2.99')
    parser.add_argument('--port',type=int,default=9010)
    args=parser.parse_args()
    try:
        deadline=time.monotonic()+25
        while True:
            state=request(args.operation,args.host,args.port)
            if args.operation!='start' or state!='stopping': break
            if time.monotonic()>deadline: raise TimeoutError('FPGA service still stopping')
            time.sleep(1)
        print('FPGA_LIFECYCLE_{}={}'.format(args.operation.upper(),state),flush=True)
        if args.operation=='start':
            if state!='starting_or_running': raise RuntimeError('unexpected FPGA lifecycle state')
            wait_for_service(args.host,9009)
            print('FPGA_SERVICE_READY: FUI1 acknowledged; FPGA PL is checked later.',flush=True)
        if args.operation=='finish' and state not in ('already_stopped','stop_scheduled_10s','stopping'):
            raise RuntimeError('unexpected FPGA lifecycle state')
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print('FPGA_LIFECYCLE_ERROR: {}'.format(exc),file=sys.stderr,flush=True)
        return 1
    return 0


if __name__=='__main__': sys.exit(main())
