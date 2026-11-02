"""Replay a recorded trace through the telemetry worker and a localhost UDP model."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import socket
import threading
import time

from .link import latency_distribution
from .reference import emulate, evaluate, load_profile, profile_crc
from .replay import samples as trace_samples
from .shadow import ShadowClient, ShadowSample, UdpTransport


class _ReferenceUdpServer:
    def __init__(self, profile):
        self.profile = profile
        self.errors = []
        self._stop = threading.Event()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind(('127.0.0.1', 0))
        self._socket.settimeout(0.05)
        self.address = self._socket.getsockname()
        self._thread = threading.Thread(target=self._serve, name='shadow-reference-udp', daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stop.is_set():
            try:
                packet, address = self._socket.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError as error:
                if not self._stop.is_set():
                    self.errors.append(str(error))
                return
            try:
                self._socket.sendto(emulate(packet, self.profile), address)
            except Exception as error:
                self.errors.append(str(error))

    def close(self):
        self._stop.set()
        self._socket.close()
        self._thread.join(timeout=2)
        if self._thread.is_alive():
            self.errors.append('reference UDP worker did not stop')
        return list(self.errors)


def run(trace_path, out_path, profile_path=None, capacity=32, timeout=1.0):
    if capacity < 1:
        raise ValueError('capacity must be positive')
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be finite and positive')

    profile = load_profile(profile_path)
    trace_path = Path(trace_path)
    trace = json.loads(trace_path.read_text(encoding='utf-8'))
    rows = list(trace_samples(trace, profile))
    if not rows:
        raise ValueError('Trace contains no rows')

    out_path = Path(out_path)
    out_path.mkdir(parents=True, exist_ok=False)
    expected = {
        sequence: evaluate(sample, profile)
        for sequence, sample in rows
    }
    accepted_sources = []
    queue_drops = set()
    results = []
    server = _ReferenceUdpServer(profile)
    client = ShadowClient(
        UdpTransport(server.address, timeout),
        on_result=results.append,
        capacity=capacity,
    )
    worker_stopped = False
    server_errors = []

    try:
        client.start()
        for offset in range(0, len(rows), capacity):
            batch = rows[offset:offset + capacity]
            for sequence, sample in batch:
                accepted_sources.append(sequence)
                if not client.submit(ShadowSample(sample.step, sample, dispatched=False)):
                    accepted_sources.pop()
                    queue_drops.add(sequence)

            target_completed = len(accepted_sources)
            deadline = time.monotonic() + timeout * (len(batch) + 1) + 1
            while time.monotonic() < deadline:
                stats = client.stats()
                completed = sum(stats[key] for key in (
                    'replied', 'timeouts', 'errors', 'response_mismatches'))
                if completed >= target_completed:
                    break
                time.sleep(0.005)
    finally:
        worker_stopped = client.close(timeout=timeout * (capacity + 1) + 1)
        server_errors = server.close()

    stats = client.stats()
    result_by_source = {}
    for result in results:
        if result.sequence < len(accepted_sources):
            result_by_source[accepted_sources[result.sequence]] = result

    result_lines = []
    for sequence, sample in rows:
        result = result_by_source.get(sequence)
        expected_result = expected[sequence]
        actual = None if result is None else dict(
            violations=result.violations,
            events=result.events,
            phase=result.phase,
        )
        matches = actual is not None and actual == {
            key: expected_result[key] for key in ('violations', 'events', 'phase')
        }
        if sequence in queue_drops:
            status = 'QUEUE_FULL'
        elif result is None:
            status = 'NO_REPLY'
        elif matches:
            status = 'MATCH'
        else:
            status = 'MISMATCH'
        result_lines.append(dict(
            source_sequence=sequence,
            step=sample.step,
            dispatched=False,
            status=status,
            expected={key: expected_result[key] for key in ('violations', 'events', 'phase')},
            actual=actual,
            expected_match=bool(matches),
            board_processing_us=None if result is None else result.board_processing_us,
            host_round_trip_us=None if result is None else result.host_round_trip_us,
        ))

    replied = [result for result in result_by_source.values()]
    matches = sum(record['expected_match'] for record in result_lines)
    mismatches = sum(record['status'] == 'MISMATCH' for record in result_lines)
    missing = sum(record['status'] in ('QUEUE_FULL', 'NO_REPLY') for record in result_lines)
    summary = dict(
        frames=len(rows),
        accepted=stats['enqueued'],
        replied=stats['replied'],
        expected_matches=matches,
        mismatches=mismatches,
        missing_responses=missing,
        queue_full_drops=stats['queue_full_drops'],
        worker_stopped=worker_stopped,
        worker_stats=stats,
        mock_processing_us=(
            latency_distribution([result.board_processing_us for result in replied])
            if replied else None
        ),
        localhost_round_trip_us=(
            latency_distribution([result.host_round_trip_us for result in replied])
            if replied else None
        ),
        result_distribution={
            'violations': {
                str(value): count for value, count in sorted(Counter(r.violations for r in replied).items())
            },
            'events': {
                str(value): count for value, count in sorted(Counter(r.events for r in replied).items())
            },
            'phases': {
                str(value): count for value, count in sorted(Counter(r.phase for r in replied).items())
            },
        },
        profile_crc='{:08x}'.format(profile_crc(profile)),
        trace_file=trace_path.name,
        transport='127.0.0.1 UDP integer reference mock',
        latency_scope='localhost process/socket path; not PL or board performance',
        mock_server_errors=server_errors,
        hardware_access=False,
        robot_commands=False,
        passed=(
            matches == len(rows) and mismatches == 0 and missing == 0
            and worker_stopped and not server_errors
            and stats['queue_full_drops'] == 0 and stats['timeouts'] == 0
            and stats['errors'] == 0 and stats['response_mismatches'] == 0
            and stats['pending'] == 0
        ),
    )

    with (out_path / 'results.jsonl').open('w', encoding='utf-8', newline='\n') as output:
        for record in result_lines:
            output.write(json.dumps(record, separators=(',', ':')) + '\n')
    (out_path / 'summary.json').write_text(
        json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', type=Path, default=Path('examples/real_trace_excerpt.json'))
    parser.add_argument('--out', type=Path, default=Path('output/shadow_replay'))
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--capacity', type=int, default=32)
    parser.add_argument('--timeout', type=float, default=1.0)
    args = parser.parse_args()
    summary = run(args.trace, args.out, args.profile, args.capacity, args.timeout)
    print(json.dumps(summary, indent=2))
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
