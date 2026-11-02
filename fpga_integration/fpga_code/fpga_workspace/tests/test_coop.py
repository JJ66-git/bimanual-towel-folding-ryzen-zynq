from dataclasses import replace
import io
import json
import math
from pathlib import Path
import socket
import subprocess
import sys
import threading
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from host.link import main as link_main, next_tick_us, response_matches, result_matches_expected, summarize_results
from host.check_udp_bad_crc import make_bad_crc_probe
from host.make_udp_probe import make_probe
from host.protocol import CHECK_CORRECTION, CHECK_START, RUNNING, FrameStream, REQUEST, RESPONSE, Sample, frame, quantize, unframe
from host.reference import emulate, evaluate, load_profile, profile_crc

ROOT = Path(__file__).resolve().parents[1]


class Checks(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile()
        start = tuple(self.profile['start'])
        self.sample = Sample(profile_crc(self.profile), 0, RUNNING, 0, 0xFFFFFFFF, 0xFFFFFFFF,
                             start, start, start, start, (0,)*14, (0,)*14)

    def changed(self, field, index, value, sample=None):
        sample = sample or self.sample
        vector = list(getattr(sample, field))
        vector[index] = value
        return replace(sample, **{field:tuple(vector)})

    def result(self, sample):
        return evaluate(sample, self.profile)

    def test_nominal_and_crc_roundtrip(self):
        packet = frame(1, 123, 10, 1234567, self.sample.pack())
        self.assertEqual(Sample.unpack(unframe(packet)[-1]), self.sample)
        self.assertEqual(len(packet), 392)
        reply = emulate(packet, self.profile)
        self.assertTrue(response_matches(reply, packet))
        self.assertEqual(RESPONSE.unpack(unframe(reply)[-1])[1], 0)
        expected = evaluate(self.sample, self.profile)
        self.assertTrue(result_matches_expected(reply, expected))
        self.assertFalse(result_matches_expected(reply, dict(expected, events=0)))

    def test_corruption_and_truncation_rejected(self):
        packet = bytearray(frame(1, 1, 1, 1, self.sample.pack()))
        packet[100] ^= 1
        with self.assertRaisesRegex(ValueError, 'CRC'):
            unframe(packet)
        with self.assertRaises(ValueError):
            unframe(packet[:100])

    def test_fragmented_stream_recovers_after_corruption_and_noise(self):
        good = frame(1, 2, 3, 4, self.sample.pack())
        bad = bytearray(good)
        bad[-1] ^= 1
        data = b'noise' + bytes(bad) + good + good
        parser = FrameStream()
        found = []
        for start in range(0, len(data), 7):
            found.extend(parser.feed(data[start:start+7]))
        self.assertEqual(found, [good, good])
        self.assertGreater(parser.errors, 0)
        self.assertFalse(parser.buffer)

    def test_header_wrong_length_and_unknown_flags(self):
        bad = replace(self.sample, flags=16)
        with self.assertRaises(ValueError):
            bad.pack()
        with self.assertRaises(ValueError):
            frame(3, 0, 0, 0, b'')

    def test_stale_response_session_sequence_tick_and_profile(self):
        request = frame(1, 1, 10, 100, self.sample.pack())
        for session, sequence, tick in ((2,10,100), (1,11,100), (1,10,101)):
            reply = frame(2, session, sequence, tick, RESPONSE.pack(self.sample.profile_crc,0,0,3,0))
            self.assertFalse(response_matches(reply, request))
        reply = frame(2, 1, 10, 100, RESPONSE.pack(0,0,0,3,0))
        self.assertFalse(response_matches(reply, request))

    def test_replay_ticks_increase_when_clock_resolution_repeats(self):
        last_tick = -1
        ticks = []
        with patch('host.link.time.monotonic_ns', return_value=7000):
            for _ in range(3):
                last_tick = next_tick_us(last_tick)
                ticks.append(last_tick)
        self.assertEqual(ticks, [7, 8, 9])

    def test_udp_probe_has_one_known_target_bounds_violation(self):
        sample = make_probe(self.profile)
        result = evaluate(sample, self.profile)
        self.assertEqual((result['violations'], result['events'], result['phase']),
                         (1, 8, 3))
        self.assertEqual(result['names'], ['target_bounds'])

    def test_udp_bad_crc_probe_is_rejected_by_frame_parser(self):
        with self.assertRaisesRegex(ValueError, 'CRC'):
            unframe(make_bad_crc_probe(self.profile))

    def test_bad_crc_cli_requires_a_matching_valid_baseline(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(('127.0.0.1', 0))
        server.settimeout(2)
        received = []
        errors = []

        def respond_to_baseline_only():
            try:
                valid, address = server.recvfrom(4096)
                received.append(valid)
                server.sendto(emulate(valid, self.profile), address)
                invalid, _ = server.recvfrom(4096)
                received.append(invalid)
            except Exception as error:
                errors.append(error)

        server_thread = threading.Thread(target=respond_to_baseline_only, daemon=True)
        server_thread.start()
        host, port = server.getsockname()
        completed = subprocess.run(
            [sys.executable, '-B', '-m', 'host.check_udp_bad_crc',
             '--udp', '{}:{}'.format(host, port), '--timeout', '0.5'],
            cwd=ROOT, capture_output=True, text=True, timeout=4)
        server_thread.join(timeout=2)
        server.close()

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertFalse(server_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(received), 2)
        self.assertEqual(unframe(received[0])[0], 1)
        with self.assertRaisesRegex(ValueError, 'CRC'):
            unframe(received[1])
        result = json.loads(completed.stdout)
        self.assertEqual(result['valid_baseline'], 'matched')
        self.assertEqual(result['invalid_request_response'], 'none')

    def test_link_cli_rejects_nonfinite_timeout(self):
        for timeout in ('nan', 'inf', '-inf'):
            with patch('sys.argv', [
                    'host.link', '--udp', '127.0.0.1:9009',
                    '--requests', 'missing.bin', '--timeout', timeout]):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as result:
                        link_main()
            self.assertEqual(result.exception.code, 2)

    def test_replay_summary_reports_latency_and_result_distributions(self):
        summary = summarize_results([
            dict(violations=0, events=8, phase=3, board_processing_us=100,
                 host_round_trip_us=1300, expected_match=True),
            dict(violations=0, events=9, phase=4, board_processing_us=200,
                 host_round_trip_us=2400, expected_match=True),
            dict(violations=1, events=9, phase=4, board_processing_us=300,
                 host_round_trip_us=3500, expected_match=False),
        ])
        self.assertEqual(summary['frames'], 3)
        self.assertEqual(summary['expected_matches'], 2)
        self.assertEqual(summary['mismatches'], 1)
        self.assertEqual(summary['board_processing_us'],
                         dict(min=100, median=200, mean=200, p95=300, max=300))
        self.assertEqual(summary['host_round_trip_us'],
                         dict(min=1300, median=2400, mean=2400, p95=3500, max=3500))
        self.assertEqual(summary['violation_masks'], {'0x00000000': 2, '0x00000001': 1})
        self.assertEqual(summary['event_masks'], {'0x00000008': 1, '0x00000009': 2})
        self.assertEqual(summary['phases'], {'3': 1, '4': 2})

    def test_nonfinite_and_overflow_rejected_before_wire(self):
        for value in (math.nan, math.inf, -math.inf, 3000):
            with self.assertRaises(ValueError):
                quantize([value]*14)
        self.assertEqual(quantize([1e-6]*14), (1,)*14)
        self.assertEqual(quantize([-1e-6]*14), (-1,)*14)

    def test_all_absolute_boundaries_and_one_unit_margin(self):
        for index in range(14):
            for value in (self.profile['lower'][index]-1, self.profile['upper'][index]+1):
                result = self.result(self.changed('target',index,value))
                self.assertFalse(result['violations'] & 1)
            for value in (self.profile['lower'][index]-2, self.profile['upper'][index]+2):
                result = self.result(self.changed('target',index,value))
                self.assertTrue(result['violations'] & 1)

    def test_joint_step_and_tracking_compare_previous_command(self):
        previous = self.sample.previous[0]
        for field, limit, bit in (('target','joint_step_limit',2), ('measured','tracking_limit',3)):
            self.assertFalse(self.result(self.changed(field,0,previous+self.profile[limit]))['violations'] & (1<<bit))
            self.assertTrue(self.result(self.changed(field,0,previous+self.profile[limit]+1))['violations'] & (1<<bit))

    def test_extreme_signed_difference_does_not_overflow(self):
        sample = self.changed('target',0,(1<<31)-1)
        sample = self.changed('previous',0,-(1<<31),sample)
        self.assertTrue(self.result(sample)['violations'] & (1<<2))

    def test_correction_amplitude_and_slew(self):
        sample = replace(self.sample, flags=RUNNING|CHECK_CORRECTION)
        for field, limit, bit in (('correction','correction_limit',4), ('correction','correction_step_limit',5)):
            modified = self.changed(field,0,self.profile[limit]+1,sample)
            self.assertTrue(self.result(modified)['violations'] & (1<<bit))

    def test_gripper_ownership_and_start_check(self):
        self.assertTrue(self.result(self.changed('target',6,1))['violations'] & (1<<6))
        sample = replace(self.sample,flags=CHECK_START)
        sample = self.changed('measured',0,sample.measured[0]+120001,sample)
        self.assertTrue(self.result(sample)['violations'] & (1<<7))

    def test_handoff_budget_manual_and_queue_are_schedule_events(self):
        self.assertEqual(self.result(replace(self.sample,step=299))['phase'],3)
        at_handoff = self.result(replace(self.sample,step=300,queue_depth=0))
        self.assertEqual(at_handoff['phase'],4)
        self.assertEqual(at_handoff['events'] & 5,5)
        self.assertEqual(self.result(replace(self.sample,flags=RUNNING|8))['phase'],4)
        self.assertTrue(self.result(replace(self.sample,step=750))['violations'] & (1<<8))

    def test_feedback_age_disabled_unknown_or_explicit_extension(self):
        self.assertFalse(self.result(replace(self.sample,feedback_age_us=10000000))['violations'] & (1<<9))
        self.assertTrue(self.result(self.sample)['events'] & (1<<3))
        profile = {**self.profile,'max_feedback_age_us':10000}
        sample = replace(self.sample,profile_crc=profile_crc(profile),feedback_age_us=10001)
        self.assertTrue(evaluate(sample,profile)['violations'] & (1<<9))

    def test_profile_mismatch_is_visible(self):
        self.assertTrue(self.result(replace(self.sample,profile_crc=0))['violations'] & (1<<10))

    def test_every_supplied_real_trace_row_roundtrips(self):
        from host.replay import samples
        total = 0
        for path in sorted((ROOT/'examples').glob('real_trace*.json')):
            trace = json.loads(path.read_text())
            for sequence, sample in samples(trace,self.profile):
                request = frame(1,7,sequence,sample.elapsed_us,sample.pack())
                self.assertEqual(Sample.unpack(unframe(request)[-1]),sample)
                self.assertTrue(response_matches(emulate(request,self.profile),request))
                total += 1
        self.assertGreaterEqual(total,1400)


if __name__ == '__main__':
    unittest.main()
