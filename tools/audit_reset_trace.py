#!/usr/bin/env python3
"""Summarize an existing reset TX/RX trace, without opening any device.

Byte definitions follow the installed Piper SDK v2 feedback messages:
arm_feedback_status.py (0x2A1), arm_feedback_low_spd.py (0x261..0x266).
SDK send returns and cached RX are evidence, not physical holding verification.
"""
import argparse
from collections import Counter
import json
from pathlib import Path


def audit(directory):
    directory = Path(directory)
    summary = json.loads((directory / 'summary.json').read_text())
    rows = [json.loads(line) for line in (directory / 'timeline.jsonl').read_text().splitlines() if line.strip()]
    rows.sort(key=lambda row: row.get('entered_monotonic_ns', row['monotonic_ns']))
    anchor = summary.get('first_control_monotonic_ns')
    if anchor is None:
        anchor = rows[0]['monotonic_ns'] if rows else 0
    transitions, previous, tx_counts, disable_commands = [], {}, Counter(), []
    for row in rows:
        kind, cid = row['kind'], row.get('can_id')
        data = bytes.fromhex(row.get('data_hex', ''))
        if len(data) != 8:
            continue
        event = dict(channel=row.get('channel'), can_id=hex(cid), raw=data.hex(),
                     after_first_control_ms=round((row['monotonic_ns'] - anchor) / 1e6, 3))
        if kind == 'tx_begin':
            tx_counts[hex(cid)] += 1
            if cid == 0x471 and data[1] == 1:
                disable_commands.append(event)
            continue
        if kind != 'rx':
            continue
        if cid == 0x2A1:
            state = tuple(data)
            event.update(kind='arm_status', control_mode=data[0], arm_status=data[1],
                         teaching_state=data[3], motion_status=data[4],
                         error_code=hex(int.from_bytes(data[6:8], 'big')),
                         joint_communication_fault=(data[1] == 5 or bool(data[7] & 0x3F)))
        elif 0x261 <= cid <= 0x266:
            state = data[5]
            event.update(kind='driver_status', joint=cid - 0x260,
                         enabled=bool(state & 0x40), fault_bits=hex(state & 0xBF),
                         voltage_V=int.from_bytes(data[:2], 'big') / 10)
        else:
            continue
        key = (row['channel'], cid)
        if previous.get(key) != state:
            transitions.append(event)
            previous[key] = state
    channels = {}
    for channel in sorted({row['channel'] for row in transitions}):
        events = [row for row in transitions if row['channel'] == channel]
        channels[channel] = dict(
            first_joint_communication_fault=next((row for row in events if row.get('joint_communication_fault')), None),
            first_disabled_driver=next((row for row in events if row.get('enabled') is False), None),
            transitions=events)
    return dict(schema='offline_reset_trace_audit_v1', trace_directory=str(directory.resolve()),
                trace_status=summary['status'], original_error=summary.get('original_error'),
                recording_error=summary.get('recording_error'), overflow_events=summary.get('overflow_events'),
                tx_counts=dict(tx_counts), recorded_disable_commands=disable_commands, channels=channels,
                robot_commands_sent=False, device_opened=False, physical_holding_verified=False,
                root_cause='Not established by this trace; do not infer supply stability from cached voltage alone.',
                scope='Recorded SDK sends and feedback only; missing external traffic or physical events cannot be excluded.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace_directory', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.trace_directory)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x') as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write('\n')
    print(json.dumps(dict(report=str(args.out), trace_status=report['trace_status'],
                          channels={key: dict(communication_fault=value['first_joint_communication_fault'] is not None,
                                              disabled_feedback=value['first_disabled_driver'] is not None)
                                    for key, value in report['channels'].items()},
                          recorded_disable_commands=len(report['recorded_disable_commands']),
                          robot_commands_sent=False), ensure_ascii=False))


if __name__ == '__main__':
    main()
