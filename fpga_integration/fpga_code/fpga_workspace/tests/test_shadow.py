import json
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from host.protocol import RESPONSE, RUNNING, Sample, frame, unframe
from host.reference import emulate, load_profile, profile_crc
from host.shadow import ShadowClient, ShadowSample, UdpTransport


class ShadowClientTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile()
        pose = tuple(self.profile['start'])
        self.sample = Sample(
            profile_crc(self.profile), 0, RUNNING, 0, 0xFFFFFFFF, 0xFFFFFFFF,
            pose, pose, pose, pose, (0,) * 14, (0,) * 14)

    def item(self, step, dispatched):
        return ShadowSample(step, self.sample, dispatched)

    def test_queue_is_bounded_and_results_preserve_dispatch_metadata(self):
        entered = threading.Event()
        release = threading.Event()
        results = []
        calls = []

        def exchange(request):
            calls.append(request)
            if len(calls) == 1:
                entered.set()
                if not release.wait(2):
                    raise TimeoutError('test gate timed out')
            return emulate(request, self.profile), 1800.0

        client = ShadowClient(exchange, results.append, capacity=1, session=123)
        client.start()
        self.assertTrue(client.submit(self.item(10, True)))
        self.assertTrue(entered.wait(1))
        self.assertTrue(client.submit(self.item(11, False)))
        self.assertFalse(client.submit(self.item(12, True)))
        release.set()
        self.assertTrue(client.close(timeout=2))

        stats = client.stats()
        self.assertEqual((stats['enqueued'], stats['sent'], stats['replied']), (2, 2, 2))
        self.assertEqual(stats['queue_full_drops'], 1)
        self.assertEqual(stats['high_watermark'], 1)
        self.assertEqual([result.step for result in results], [10, 11])
        self.assertEqual([result.dispatched for result in results], [True, False])
        requests = [unframe(packet) for packet in calls]
        self.assertEqual([request[2] for request in requests], [0, 1])
        self.assertLess(requests[0][3], requests[1][3])

    def test_timeout_and_mismatched_reply_are_counted_and_worker_continues(self):
        calls = []

        def exchange(request):
            calls.append(request)
            if len(calls) == 1:
                raise TimeoutError('injected timeout')
            _, session, sequence, tick, payload = unframe(request)
            return frame(2, session, sequence + 1, tick,
                         RESPONSE.pack(self.sample.profile_crc, 0, 8, 3, 0)), 2000.0

        client = ShadowClient(exchange, capacity=2, session=456)
        client.start()
        self.assertTrue(client.submit(self.item(20, False)))
        self.assertTrue(client.submit(self.item(21, True)))
        self.assertTrue(client.close(timeout=2))

        stats = client.stats()
        self.assertEqual(stats['sent'], 2)
        self.assertEqual(stats['timeouts'], 1)
        self.assertEqual(stats['response_mismatches'], 1)
        self.assertEqual(stats['replied'], 0)
        self.assertEqual(stats['pending'], 0)

    def test_submit_after_close_is_counted_without_enqueue(self):
        client = ShadowClient(lambda request: (emulate(request, self.profile), 1.0), capacity=1)
        self.assertTrue(client.close(timeout=0))
        self.assertFalse(client.submit(self.item(30, True)))
        self.assertEqual(client.stats()['stopped_drops'], 1)
        self.assertEqual(client.stats()['enqueued'], 0)
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            client.start()

    def test_udp_transport_and_worker_roundtrip_against_localhost_mock(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(('127.0.0.1', 0))
        server.settimeout(2)
        server_errors = []

        def respond_once():
            try:
                request, address = server.recvfrom(4096)
                server.sendto(emulate(request, self.profile), address)
            except Exception as error:
                server_errors.append(error)

        server_thread = threading.Thread(target=respond_once, daemon=True)
        server_thread.start()
        results = []
        client = ShadowClient(
            UdpTransport(server.getsockname(), timeout=1),
            results.append,
            capacity=1,
            session=789,
        )
        client.start()
        try:
            self.assertTrue(client.submit(self.item(40, True)))
            self.assertTrue(client.close(timeout=2))
        finally:
            server_thread.join(timeout=2)
            server.close()

        self.assertFalse(server_thread.is_alive())
        self.assertEqual(server_errors, [])
        self.assertEqual(client.stats()['replied'], 1)
        self.assertEqual(client.stats()['errors'], 0)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].session, 789)
        self.assertEqual(results[0].sequence, 0)
        self.assertEqual(results[0].step, 40)
        self.assertTrue(results[0].dispatched)
        self.assertEqual(results[0].profile_crc, self.sample.profile_crc)
        self.assertEqual(results[0].violations, 0)
        self.assertGreater(results[0].host_round_trip_us, 0)

    def test_submit_snapshots_mutable_vectors_before_enqueue_returns(self):
        entered = threading.Event()
        release = threading.Event()
        requests = []

        def exchange(request):
            requests.append(request)
            if len(requests) == 1:
                entered.set()
                if not release.wait(2):
                    raise TimeoutError('test exchange gate timed out')
            return emulate(request, self.profile), 100.0

        client = ShadowClient(exchange, capacity=1, session=792)
        client.start()
        self.assertTrue(client.submit(self.item(60, False)))
        self.assertTrue(entered.wait(1))

        target = list(self.sample.target)
        mutable_sample = Sample(
            self.sample.profile_crc, self.sample.step, self.sample.flags,
            self.sample.elapsed_us, self.sample.feedback_age_us, self.sample.queue_depth,
            target, self.sample.measured, self.sample.previous, self.sample.act,
            self.sample.correction, self.sample.previous_correction)
        self.assertTrue(client.submit(ShadowSample(61, mutable_sample, True)))
        expected_target = target[0]
        target[0] += 1

        release.set()
        self.assertTrue(client.close(timeout=2))
        sent_sample = Sample.unpack(unframe(requests[1])[-1])
        self.assertEqual(sent_sample.target[0], expected_target)

    def test_invalid_snapshot_is_counted_without_escaping_submit(self):
        client = ShadowClient(lambda request: (emulate(request, self.profile), 1.0))
        client.start()
        invalid_sample = Sample(
            self.sample.profile_crc, self.sample.step, self.sample.flags,
            self.sample.elapsed_us, self.sample.feedback_age_us, self.sample.queue_depth,
            (0,) * 13, self.sample.measured, self.sample.previous, self.sample.act,
            self.sample.correction, self.sample.previous_correction)

        self.assertFalse(client.submit(ShadowSample(62, invalid_sample, True)))
        self.assertEqual(client.stats()['errors'], 1)
        self.assertEqual(client.stats()['enqueued'], 0)
        self.assertTrue(client.close(timeout=2))

    def test_shadow_transport_timeouts_must_be_finite_and_positive(self):
        from host.shadow_replay import run

        for timeout in (float('nan'), float('inf'), float('-inf'), 0):
            with self.assertRaises(ValueError):
                UdpTransport(('127.0.0.1', 9009), timeout=timeout)
            with self.assertRaises(ValueError):
                run('missing-trace.json', 'unused-output', timeout=timeout)

    def test_udp_transport_skips_mismatched_datagram_and_accepts_matching_reply(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(('127.0.0.1', 0))
        server.settimeout(2)
        server_errors = []

        def respond_with_wrong_sequence():
            try:
                request, address = server.recvfrom(4096)
                _, session, sequence, tick, _ = unframe(request)
                server.sendto(frame(
                    2, session, sequence + 1, tick,
                    RESPONSE.pack(self.sample.profile_crc, 0, 8, 3, 0)), address)
                server.sendto(emulate(request, self.profile), address)
            except Exception as error:
                server_errors.append(error)

        server_thread = threading.Thread(target=respond_with_wrong_sequence, daemon=True)
        server_thread.start()
        client = ShadowClient(
            UdpTransport(server.getsockname(), timeout=1),
            capacity=1,
            session=790,
        )
        client.start()
        try:
            self.assertTrue(client.submit(self.item(41, False)))
            self.assertTrue(client.close(timeout=2))
        finally:
            server_thread.join(timeout=2)
            server.close()

        self.assertFalse(server_thread.is_alive())
        self.assertEqual(server_errors, [])
        stats = client.stats()
        self.assertEqual(stats['response_mismatches'], 1)
        self.assertEqual(stats['timeouts'], 0)
        self.assertEqual(stats['replied'], 1)

    def test_close_deadline_drops_queued_samples_and_stops_after_inflight(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def exchange(request):
            calls.append(request)
            entered.set()
            if len(calls) == 1 and not release.wait(2):
                raise TimeoutError('test exchange gate timed out')
            return emulate(request, self.profile), 100.0

        client = ShadowClient(exchange, capacity=2, session=791)
        client.start()
        self.assertTrue(client.submit(self.item(50, False)))
        self.assertTrue(entered.wait(1))
        self.assertTrue(client.submit(self.item(51, False)))
        self.assertTrue(client.submit(self.item(52, True)))

        self.assertFalse(client.close(timeout=0.01))
        self.assertEqual(client.stats()['shutdown_drops'], 2)
        self.assertEqual(client.stats()['pending'], 0)

        release.set()
        self.assertTrue(client.close(timeout=2))
        stats = client.stats()
        self.assertEqual(len(calls), 1)
        self.assertEqual(stats['sent'], 1)
        self.assertEqual(stats['shutdown_drops'], 2)
        self.assertEqual(stats['pending'], 0)

    def test_full_trace_shadow_replay_cli_matches_reference_without_hardware(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            out_path = Path(temp_dir) / 'nested' / 'shadow-replay'
            completed = subprocess.run(
                [
                    sys.executable, '-B', '-m', 'host.shadow_replay',
                    '--trace', str(project / 'examples/real_trace_excerpt.json'),
                    '--out', str(out_path),
                ],
                cwd=project,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

            summary = json.loads((out_path / 'summary.json').read_text(encoding='utf-8'))
            records = [
                json.loads(line)
                for line in (out_path / 'results.jsonl').read_text(encoding='utf-8').splitlines()
            ]

        self.assertEqual(summary['frames'], 667)
        self.assertEqual(summary['accepted'], 667)
        self.assertEqual(summary['replied'], 667)
        self.assertEqual(summary['expected_matches'], 667)
        self.assertEqual(summary['mismatches'], 0)
        self.assertEqual(summary['missing_responses'], 0)
        self.assertEqual(summary['queue_full_drops'], 0)
        self.assertEqual(summary['worker_stats']['timeouts'], 0)
        self.assertEqual(summary['worker_stats']['errors'], 0)
        self.assertLessEqual(summary['worker_stats']['high_watermark'], 32)
        self.assertEqual(summary['worker_stats']['pending'], 0)
        self.assertEqual(summary['result_distribution']['violations'], {'0': 667})
        self.assertEqual(summary['result_distribution']['events'], {'8': 300, '9': 367})
        self.assertEqual(summary['result_distribution']['phases'], {'3': 300, '4': 367})
        self.assertFalse(summary['hardware_access'])
        self.assertFalse(summary['robot_commands'])
        self.assertTrue(summary['passed'])
        self.assertEqual(len(records), 667)
        self.assertTrue(all(record['expected_match'] for record in records))


if __name__ == '__main__':
    unittest.main()
