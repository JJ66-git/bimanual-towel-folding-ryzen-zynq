#!/usr/bin/env python3
"""Check receive-only output from the existing preparation command."""
import json
import sys
from pathlib import Path


def check(path):
    rows = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        for side in ('left', 'right'):
            prefix = 'RECEIVE_ONLY '+side+'='
            if line.startswith(prefix):
                rows[side] = json.loads(line[len(prefix):])
    for side in ('left', 'right'):
        row = rows.get(side)
        if row is None or row.get('rx_frames', 0) <= 0 or row.get('gripper_frames', 0) <= 0:
            raise RuntimeError(side+' 侧未读到完整关节/夹爪反馈；检查供电和 CAN 连接。')
        if set(row.get('joint_enable', {})) != {str(i) for i in range(1, 7)}:
            raise RuntimeError(side+' 侧缺少部分关节状态反馈。')
        if row.get('error_frames', 0):
            raise RuntimeError(side+' 侧收到 CAN 错误帧，请检查。')
    print('ARM_FEEDBACK_READY: 左右两侧均收到关节和夹爪反馈；不代表任务已执行。', flush=True)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('用法：check_preparation_result.py LOG')
    check(sys.argv[1])
