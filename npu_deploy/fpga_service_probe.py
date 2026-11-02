#!/usr/bin/env python3
"""Probe the board service using an FUI1 status acknowledgement, not FPG1."""
import argparse
import ipaddress
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT/'fpga_integration/fpga_code/fpga_workspace', ROOT/'fpga'):
    if (directory/'host/task_status.py').is_file():
        sys.path.insert(0, str(directory))
        break
else:
    raise RuntimeError('未找到 FPGA 协议源码')
from host.task_status import Client


def probe(address):
    host, sep, port = address.rpartition(':')
    if not sep:
        raise ValueError('FPGA 地址格式必须是 IPv4:端口')
    host = str(ipaddress.IPv4Address(host))
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError('FPGA 端口无效')
    deadline = time.monotonic() + 8
    while True:
        client = Client((host, port), timeout=2.0)
        try:
            reply = client.send('WAITING', 'IDLE', 'HOST')
            break
        except (TimeoutError, OSError):
            if time.monotonic() >= deadline:
                raise
            print('FPGA 状态通道暂未响应，等待上一会话空闲后重试……', flush=True)
            time.sleep(0.25)
        finally:
            client.close()
    print('FPGA_SERVICE_ACK='+json.dumps(dict(address=[host,port],reply=reply,
        meaning='PYNQ 服务收到并确认 FUI1；真正 PL 检查需看任务中的 FPG1 回包。'),ensure_ascii=False),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('address')
    probe(parser.parse_args().address)
