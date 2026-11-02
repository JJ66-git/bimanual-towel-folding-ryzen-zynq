#!/usr/bin/env python3
"""Two FUI1 and PL round trips, with no robot or camera access."""
import argparse
from datetime import datetime
import ipaddress
from pathlib import Path
import time

from fpga_service_probe import probe
from fpga_live_shadow import FpgaShadow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='192.168.2.99')
    parser.add_argument('--port', type=int, default=9009)
    args = parser.parse_args()
    host = str(ipaddress.IPv4Address(args.host))
    if not 1 <= args.port <= 65535:
        parser.error('invalid UDP port')
    address = (host, args.port)
    root = Path(__file__).resolve().parent / 'artifacts' / 'fpga_repeat_check'
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    for run in (1, 2):
        print(f'REPEAT_CHECK round={run}/2: FUI1 服务确认', flush=True)
        probe(f'{host}:{args.port}')
        shadow = FpgaShadow(address, root / f'{stamp}_{run}', max_actions=750, handoff_step=300)
        try:
            shadow.probe()
        finally:
            shadow.close()
        print(f'REPEAT_CHECK round={run}/2: FPGA PL 回包与参考一致', flush=True)
        if run == 1:
            print('等待 6 秒，让前一诊断会话空闲过期……', flush=True)
            time.sleep(6)
    print('FPGA_REPEAT_CHECK_PASSED: 两次独立 FUI1 与 PL 旁路探针均通过；未连接机械臂。', flush=True)


if __name__ == '__main__':
    main()
