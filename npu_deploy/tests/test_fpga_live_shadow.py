import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fpga_live_shadow as shadow
from host.protocol import Sample, RESPONSE, frame, unframe
from host.reference import emulate, evaluate


class FakeTransport:
    def __init__(self, address, timeout, profile):
        self.profile = profile
        self.expected = None
        self.requests = []

    def exchange(self, packet):
        sample = Sample.unpack(unframe(packet)[4])
        self.requests.append(sample)
        self.expected = evaluate(sample, self.profile)
        return emulate(packet, self.profile), 120.0

    def close(self):
        pass


class MismatchTransport(FakeTransport):
    def exchange(self, packet):
        good, elapsed = super().exchange(packet)
        _, session, sequence, tick, payload = unframe(good)
        profile, violations, events, phase, board_us = RESPONSE.unpack(payload)
        wrong = frame(2, session, sequence, tick,
                      RESPONSE.pack(profile, violations | 1, events, phase, board_us))
        return wrong, elapsed


class ShadowTests(unittest.TestCase):
    def test_snapshot_reference_and_dispatched_flag(self):
        with tempfile.TemporaryDirectory() as parent, patch.object(shadow, 'ReferenceTransport', FakeTransport):
            monitor = shadow.FpgaShadow(('127.0.0.1',9009), Path(parent)/'run',
                                        max_actions=900, handoff_step=300)
            values = [0.0]*14
            self.assertTrue(monitor.submit(step=1, elapsed=.1, target=values, measured=values,
                previous=values, act=values, correction=values, previous_correction=values,
                blended=False, dispatched=False))
            values[0] = 0.9
            deadline = time.monotonic()+2
            while monitor.client.stats()['replied'] < 1 and time.monotonic() < deadline:
                time.sleep(.005)
            monitor.close()
            row = json.loads((monitor.output/'checks.jsonl').read_text().strip())
            self.assertEqual(monitor.transport.requests[0].target[0], 0)
            self.assertEqual(monitor.transport.requests[0].feedback_age_us, shadow.UNKNOWN)
            self.assertFalse(row['dispatched'])
            self.assertTrue(row['reference_match'])
            summary = json.loads((monitor.output/'summary.json').read_text())
            self.assertEqual(summary['replied'], 1)
            self.assertEqual(summary['reference_matches'], 1)
            self.assertEqual(summary['reference_mismatches'], 0)
            self.assertEqual(summary['round_trip_ms']['samples'], 1)
            self.assertEqual(summary['round_trip_ms']['maximum'], 0.12)

    def test_probe_shares_session_with_task_and_is_not_counted_as_task(self):
        with tempfile.TemporaryDirectory() as parent, patch.object(shadow, 'ReferenceTransport', FakeTransport):
            monitor = shadow.FpgaShadow(('127.0.0.1',9009), Path(parent)/'run',
                                        max_actions=750, handoff_step=300)
            probe = monitor.probe()
            self.assertTrue(probe['reference_match'])
            self.assertEqual(probe['kind'], 'preflight_probe')
            values = [0.0]*14
            self.assertTrue(monitor.submit(step=0, elapsed=0, target=values, measured=values,
                previous=values, act=values, correction=values, previous_correction=values,
                blended=False, dispatched=False))
            deadline = time.monotonic()+2
            while monitor.client.stats()['replied'] < 2 and time.monotonic() < deadline:
                time.sleep(.005)
            monitor.close()
            lines = [json.loads(line) for line in (monitor.output/'checks.jsonl').read_text().splitlines()]
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0]['session'], lines[1]['session'])
            self.assertEqual([r['sequence'] for r in lines], [0, 1])
            self.assertEqual(lines[1]['kind'], 'task_sample')
            summary = json.loads((monitor.output/'summary.json').read_text())
            self.assertTrue(summary['probe_reply'])
            self.assertEqual(summary['checked'], 1)
            self.assertEqual(summary['replied'], 2)

    def test_bad_probe_is_rejected(self):
        with tempfile.TemporaryDirectory() as parent, patch.object(shadow, 'ReferenceTransport', MismatchTransport):
            monitor = shadow.FpgaShadow(('127.0.0.1',9009), Path(parent)/'run',
                                        max_actions=750, handoff_step=300)
            try:
                with self.assertRaisesRegex(RuntimeError, '不一致'):
                    monitor.probe()
            finally:
                monitor.close()

    def test_reference_mismatch_is_reported(self):
        with tempfile.TemporaryDirectory() as parent, patch.object(shadow, 'ReferenceTransport', MismatchTransport):
            monitor = shadow.FpgaShadow(('127.0.0.1',9009), Path(parent)/'run',
                                        max_actions=750, handoff_step=300)
            values = [0.0]*14
            self.assertTrue(monitor.submit(step=0, elapsed=0, target=values, measured=values,
                previous=values, act=values, correction=values, previous_correction=values,
                blended=False, dispatched=True))
            deadline = time.monotonic()+2
            while monitor.client.stats()['replied'] < 1 and time.monotonic() < deadline:
                time.sleep(.005)
            monitor.close()
            summary = json.loads((monitor.output/'summary.json').read_text())
            self.assertEqual(summary['reference_mismatches'], 1)
            self.assertEqual(summary['reference_matches'], 0)
            self.assertEqual(summary['violations'], 1)

    def test_invalid_values_do_not_enter_queue(self):
        with tempfile.TemporaryDirectory() as parent, patch.object(shadow, 'ReferenceTransport', FakeTransport):
            monitor = shadow.FpgaShadow(('127.0.0.1',9009), Path(parent)/'run',
                                        max_actions=750, handoff_step=300)
            valid = [0.0]*14
            bad = [float('nan')]+valid[1:]
            self.assertFalse(monitor.submit(step=0, elapsed=0, target=bad, measured=valid,
                previous=valid, act=valid, correction=valid, previous_correction=valid,
                blended=False, dispatched=True))
            monitor.close()
            self.assertEqual(monitor.local_errors, 1)
            self.assertEqual(monitor.client.stats()['enqueued'], 0)


if __name__ == '__main__':
    unittest.main()
