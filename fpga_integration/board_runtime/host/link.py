"""Send recorded checker packets over UART or UDP; never issue robot commands."""
import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import secrets
import select
import socket
import statistics
import time

from .protocol import FrameStream, RESPONSE, frame, unframe


def next_tick_us(previous_tick):
    return max(time.monotonic_ns() // 1000, previous_tick + 1)


def response_matches(packet, request):
    kind, session, sequence, tick, payload = unframe(packet)
    _, wanted_session, wanted_sequence, wanted_tick, wanted_payload = unframe(request)
    if (kind, session, sequence, tick) != (2, wanted_session, wanted_sequence, wanted_tick):
        return False
    profile, *_ = RESPONSE.unpack(payload)
    return profile == int.from_bytes(wanted_payload[:4], 'little')


def result_matches_expected(packet, expected):
    *_, payload = unframe(packet)
    _, violations, events, phase, _ = RESPONSE.unpack(payload)
    return (violations, events, phase) == (
        int(expected['violations']), int(expected['events']), int(expected['phase']))


def summarize_results(records):
    if not records:
        raise ValueError('Cannot summarize an empty response set')
    board_latencies = [record['board_processing_us'] for record in records]
    round_trip_latencies = [record['host_round_trip_us'] for record in records]
    matches = sum(record['expected_match'] for record in records)
    return dict(
        frames=len(records),
        expected_matches=matches,
        mismatches=len(records) - matches,
        board_processing_us=latency_distribution(board_latencies),
        host_round_trip_us=latency_distribution(round_trip_latencies),
        violation_masks={
            '0x{:08x}'.format(mask): count
            for mask, count in sorted(Counter(record['violations'] for record in records).items())
        },
        event_masks={
            '0x{:08x}'.format(mask): count
            for mask, count in sorted(Counter(record['events'] for record in records).items())
        },
        phases={
            str(phase): count
            for phase, count in sorted(Counter(record['phase'] for record in records).items())
        },
        robot_commands=False,
    )


def latency_distribution(values):
    ordered = sorted(values)
    if not ordered:
        raise ValueError('Cannot summarize an empty latency set')
    return dict(
        min=ordered[0],
        median=statistics.median(ordered),
        mean=round(statistics.mean(ordered), 3),
        p95=ordered[math.ceil(0.95 * len(ordered)) - 1],
        max=ordered[-1],
    )


def uart_exchange(path, baud, packets, timeout):
    # Linux termios, no external pyserial dependency. Board receiver is separate RTL.
    import fcntl
    import termios
    speed = getattr(termios, 'B'+str(baud), None)
    if speed is None:
        raise ValueError('Baud rate unsupported by this Linux termios')
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    old = None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        old = termios.tcgetattr(fd)
        attrs = termios.tcgetattr(fd)
        attrs[0] = attrs[1] = attrs[3] = 0
        attrs[2] = termios.CLOCAL | termios.CREAD | termios.CS8
        attrs[4] = attrs[5] = speed
        attrs[6][termios.VMIN] = attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        stream = FrameStream()
        for request in packets:
            started_ns = time.perf_counter_ns()
            deadline = time.monotonic()+timeout
            pending = memoryview(request)
            while pending:
                remaining = deadline-time.monotonic()
                if remaining <= 0 or not select.select([], [fd], [], remaining)[1]:
                    raise TimeoutError('UART write timeout')
                try:
                    pending = pending[os.write(fd, pending):]
                except BlockingIOError:
                    continue
            response = None
            while response is None:
                remaining = deadline-time.monotonic()
                if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                    raise TimeoutError('No matching UART reply; no motion decision produced')
                try:
                    data = os.read(fd, 4096)
                except BlockingIOError:
                    continue
                for packet in stream.feed(data):
                    if response_matches(packet, request):
                        response = packet
                        break
            round_trip_us = (time.perf_counter_ns() - started_ns) / 1000
            yield response, round_trip_us
    finally:
        try:
            if old is not None:
                termios.tcsetattr(fd, termios.TCSANOW, old)
        finally:
            os.close(fd)


def udp_exchange(address, packets, timeout):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(address)
        for request in packets:
            started_ns = time.perf_counter_ns()
            deadline = time.monotonic()+timeout
            sock.send(request)
            while True:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('No matching UDP reply; no motion decision produced')
                sock.settimeout(remaining)
                try:
                    response = sock.recv(4096)
                    if response_matches(response, request):
                        break
                except ValueError:
                    continue
            round_trip_us = (time.perf_counter_ns() - started_ns) / 1000
            yield response, round_trip_us


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--serial')
    group.add_argument('--udp', metavar='HOST:PORT')
    parser.add_argument('--baud', type=int, default=460800)
    parser.add_argument('--timeout', type=float, default=1.0, help='Replay timeout, not production deadline')
    parser.add_argument('--requests', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=1)
    parser.add_argument('--expected', type=Path,
                        help='optional JSONL reference results to compare in request order')
    parser.add_argument('--summary-json', type=Path,
                        help='write comparison and board-processing statistics as JSON')
    args = parser.parse_args()
    if args.limit < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('limit and timeout must be positive')
    if args.summary_json and not args.expected:
        parser.error('--summary-json requires --expected')
    if args.summary_json:
        if not args.summary_json.parent.is_dir():
            parser.error('summary directory does not exist: {}'.format(args.summary_json.parent))
        if args.summary_json.exists():
            parser.error('summary file already exists: {}'.format(args.summary_json))
    stream = FrameStream()
    packets = stream.feed(args.requests.read_bytes())
    if stream.errors or stream.buffer:
        parser.error('Input contains corrupt or incomplete packets')
    selected = []
    session = secrets.randbits(32)
    last_tick = -1
    for index, packet in enumerate(packets[:args.limit]):
        kind, _, _, _, payload = unframe(packet)
        if kind != 1:
            parser.error('Input must contain request frames only')
        last_tick = next_tick_us(last_tick)
        selected.append(frame(1, session, index, last_tick, payload))
    if not selected:
        parser.error('No request frames found')
    expected_rows = None
    if args.expected:
        try:
            expected_rows = [json.loads(line) for line in args.expected.read_text().splitlines()
                             if line.strip()]
            if len(expected_rows) < len(selected):
                raise ValueError('Expected results contain fewer rows than selected requests')
            for index, row in enumerate(expected_rows[:len(selected)]):
                if int(row['sequence']) != index:
                    raise ValueError('Expected result sequence is not contiguous at {}'.format(index))
                for field in ('violations', 'events', 'phase'):
                    int(row[field])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            parser.error('Invalid expected results: {}'.format(error))
    if args.serial:
        replies = uart_exchange(args.serial, args.baud, selected, args.timeout)
    else:
        host, port = args.udp.rsplit(':', 1)
        replies = udp_exchange((host, int(port)), selected, args.timeout)
    mismatches = 0
    records = []
    for index, (response, round_trip_us) in enumerate(replies):
        *_, payload = unframe(response)
        profile, violations, events, phase, latency_us = RESPONSE.unpack(payload)
        if expected_rows is None:
            print(f'profile={profile:08x} violations={violations:08x} events={events:08x} '
                  f'phase={phase} reported_latency_us={latency_us}')
            continue
        expected = expected_rows[index]
        matches = result_matches_expected(response, expected)
        label = 'PASS' if matches else 'FAIL'
        print(f'seq={index} {label} violations={violations:08x} events={events:08x} '
              f'phase={phase} reported_latency_us={latency_us}')
        mismatches += not matches
        records.append(dict(
            violations=violations,
            events=events,
            phase=phase,
            board_processing_us=latency_us,
            host_round_trip_us=round_trip_us,
            expected_match=matches,
        ))
    if expected_rows is not None:
        if args.summary_json:
            summary = summarize_results(records)
            summary['transport'] = 'udp' if args.udp else 'serial'
            summary['expected_file'] = str(args.expected)
            summary['summary_file'] = str(args.summary_json)
            args.summary_json.write_text(json.dumps(summary, indent=2) + '\n')
            latency = summary['board_processing_us']
            print('SUMMARY: frames={} board_processing_us min={} median={} mean={} p95={} max={}'.format(
                summary['frames'], latency['min'], latency['median'], latency['mean'],
                latency['p95'], latency['max']))
            round_trip = summary['host_round_trip_us']
            print('SUMMARY: host_round_trip_us min={} median={} mean={} p95={} max={}'.format(
                round_trip['min'], round_trip['median'], round_trip['mean'],
                round_trip['p95'], round_trip['max']))
            print('SUMMARY_JSON: {}'.format(args.summary_json))
        if mismatches:
            raise SystemExit('FAIL: {}/{} responses differ from {}'.format(
                mismatches, len(selected), args.expected))
        print('PASS: compared {}/{} responses against {}'.format(
            len(selected), len(selected), args.expected))


if __name__ == '__main__':
    main()
