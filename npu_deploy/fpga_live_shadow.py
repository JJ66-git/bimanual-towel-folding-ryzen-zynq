#!/usr/bin/env python3
"""FPG1 diagnostic shadow for the existing controller; no robot control APIs."""
from dataclasses import asdict
import json
import math
import os
import statistics
import threading
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / 'fpga_integration/fpga_code/fpga_workspace', ROOT / 'fpga'):
    if (directory / 'host/shadow.py').is_file():
        sys.path.insert(0, str(directory))
        break
else:
    raise RuntimeError('FPGA shadow source not found')

from host.protocol import CHECK_CORRECTION, RUNNING, UNKNOWN, Sample, quantize, unframe
from host.reference import evaluate, load_profile, profile_crc
from host.shadow import ShadowClient, ShadowSample, UdpTransport


class ReferenceTransport(UdpTransport):
    """Compute a reference in the worker thread, separately from robot control."""
    def __init__(self, address, timeout, profile):
        super().__init__(address, timeout)
        self.profile = profile
        self.expected = None

    def exchange(self, request):
        sample = Sample.unpack(unframe(request)[4])
        self.expected = evaluate(sample, self.profile)
        return super().exchange(request)


class FpgaShadow:
    def __init__(self, address, output, *, max_actions, handoff_step, timeout=0.3):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=False)
        self.profile = load_profile()
        self.crc = profile_crc(self.profile)
        self.events = (self.output / 'checks.jsonl').open('x', encoding='utf-8', buffering=1)
        self.transport = ReferenceTransport(address, timeout, self.profile)
        self.client = ShadowClient(self.transport, on_result=self.record, capacity=32)
        self.local_errors = 0
        self._probe_event = threading.Event()
        self._probe_result = None
        self._result_lock = threading.Lock()
        self._result_counts = dict(checked=0, reference_matches=0, reference_mismatches=0, violations=0)
        self._rtt_ms = []
        self._board_ms = []
        self.client.start()
        self.metadata = dict(protocol='FPG1', address=list(address), profile_crc=f'{self.crc:08x}',
            fpga_budget=self.profile['max_actions'], controller_budget=max_actions,
            fpga_handoff=self.profile['handoff_step'], controller_handoff=handoff_step,
            watchdog='FPG1 does not feed FWD1 watchdog',
            scope='diagnostic shadow only; return never controls or authorizes robot')
        (self.output / 'metadata.json').write_text(json.dumps(self.metadata, indent=2)+'\n', encoding='utf-8')
        print('FPGA_SHADOW_START='+json.dumps(self.metadata), flush=True)
        if self.profile['max_actions'] != max_actions or self.profile['handoff_step'] != handoff_step:
            print('FPGA_SHADOW_NOTICE: FPGA profile schedule differs from controller; flags use FPGA profile.', flush=True)

    def record(self, result):
        expected = self.transport.expected
        record = asdict(result)
        record.update(expected=expected, reference_match=bool(expected is not None and
            all(record[key] == expected[key] for key in ('violations', 'events', 'phase'))))
        record['kind'] = 'preflight_probe' if result.step == UNKNOWN and not result.dispatched else 'task_sample'
        self.events.write(json.dumps(record, allow_nan=False)+'\n')
        if record['kind'] == 'preflight_probe':
            self._probe_result = record
            self._probe_event.set()
            print('FPGA_PL_PROBE='+json.dumps(record, allow_nan=False), flush=True)
            return
        with self._result_lock:
            self._result_counts['checked'] += 1
            first_task_reply = self._result_counts['checked'] == 1
            self._result_counts['reference_matches' if record['reference_match'] else 'reference_mismatches'] += 1
            self._result_counts['violations'] += bool(result.violations)
            self._rtt_ms.append(result.host_round_trip_us / 1000)
            self._board_ms.append(result.board_processing_us / 1000)
        if first_task_reply:
            print('FPGA_SHADOW_FIRST_REPLY='+json.dumps(record, allow_nan=False), flush=True)

    def probe(self, timeout=2.0):
        """Use the same socket/session as future task data and verify the PL reply."""
        start = tuple(self.profile['start'])
        zeros = (0,) * 14
        sample = Sample(self.crc, UNKNOWN, 0, 0, UNKNOWN, UNKNOWN,
                        start, start, start, start, zeros, zeros)
        if not self.client.submit(ShadowSample(UNKNOWN, sample, False)):
            raise RuntimeError('FPGA PL 探针无法加入队列。')
        if not self._probe_event.wait(timeout):
            raise TimeoutError('FPGA PL 探针未收到匹配回包；板端可能需要启动或重启服务。')
        result = self._probe_result
        if not result['reference_match'] or result['violations']:
            raise RuntimeError('FPGA PL 探针与整数参考不一致。')
        return result

    def submit(self, *, step, elapsed, target, measured, previous, act, correction,
               previous_correction, blended, dispatched):
        try:
            sample = Sample(self.crc, int(step), RUNNING | (CHECK_CORRECTION if blended else 0),
                max(0, int(elapsed * 1_000_000)), UNKNOWN, UNKNOWN,
                *(quantize(v) for v in (target, measured, previous, act, correction, previous_correction)))
            return self.client.submit(ShadowSample(int(step), sample, bool(dispatched)))
        except Exception as exc:
            self.local_errors += 1
            if self.local_errors == 1:
                print('FPGA_SHADOW_SAMPLE_ERROR='+repr(exc), flush=True)
            return False

    def close(self):
        stopped = self.client.close(timeout=2.0)
        stats = self.client.stats()
        with self._result_lock:
            stats.update(self._result_counts)
            for name, values in (('round_trip_ms', self._rtt_ms), ('board_processing_ms', self._board_ms)):
                ordered = sorted(values)
                stats[name] = None if not ordered else dict(
                    median=statistics.median(ordered),
                    p95_nearest_rank=ordered[math.ceil(.95 * len(ordered)) - 1],
                    maximum=ordered[-1], samples=len(ordered))
        stats.update(local_errors=self.local_errors, worker_stopped=stopped,
                     probe_reply=self._probe_result is not None, output=str(self.output))
        if stopped:
            self.events.close()
        (self.output / 'summary.json').write_text(json.dumps(stats, indent=2)+'\n', encoding='utf-8')
        print('FPGA_SHADOW_SUMMARY='+json.dumps(stats), flush=True)


def from_environment(*, max_actions, handoff_step):
    value = os.environ.get('AIPC_FPGA_SHADOW')
    if not value:
        return None
    host, separator, port = value.rpartition(':')
    if not separator:
        raise ValueError('AIPC_FPGA_SHADOW must be IPv4:port')
    import ipaddress
    host = str(ipaddress.IPv4Address(host))
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError('Invalid FPGA UDP port')
    output = ROOT / 'npu_deploy/artifacts/fpga_shadow' / f'run_{time.time_ns()}_{os.getpid()}'
    return FpgaShadow((host, port), output, max_actions=max_actions, handoff_step=handoff_step)
