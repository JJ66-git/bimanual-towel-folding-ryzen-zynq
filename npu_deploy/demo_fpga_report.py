#!/usr/bin/env python3
"""Summarize and verify the current synthetic FPGA client's returned records."""
import json
import sys
from pathlib import Path


def main(directory):
    p = Path(directory)
    summary = json.loads((p / 'summary.json').read_text(encoding='utf-8'))
    rows = [json.loads(line) for line in (p / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    checks = [r for r in rows if r.get('event') == 'diagnostic_exchange' and r.get('operation') == 'allow']
    if not summary.get('passed') or summary.get('input_source') != 'synthetic_test' or len(checks) != 4:
        raise ValueError('客户端日志不完整，不能报告 FPGA 合成演示成功。')
    for row in checks:
        value = row['response']
        if value.get('build_id') != 0x46504732 or value.get('violations') != 0:
            raise ValueError('硬件标识或检查结果与预期不一致。')
    print('合成动作检查回包：')
    for row in checks:
        value = row['response']
        print('  {}: build_id=0x{:08x} violations=0x{:08x} phase={} UDP往返={:.3f} ms'.format(
            row['label'], value['build_id'], value['violations'], value['phase'], row['host_round_trip_us'] / 1000))
    print('以上是服务返回的合成数据结果；完整 PS/PL 运行应结合板端日志和 overlay 核对。')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('用法：python3 demo_fpga_report.py CLIENT_LOG_DIR')
    main(sys.argv[1])
