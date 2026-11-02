import struct
import unittest
import zlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from host import watchdog_protocol as wire
from host.protocol import RUNNING, Sample
from host.reference import load_profile, profile_crc
from board.watchdog_monitor import read_u64


class WatchdogProtocolTests(unittest.TestCase):
    def test_overlay_clock_metadata_matches_captured_boot_pll(self):
        root = Path(__file__).resolve().parents[1]
        boot = json.loads((root/'tests/fixtures/board_clock_baseline.json').read_text())
        tree = ET.parse(root/'overlay/own_hybrid_motion_guard_watchdog.hwh')
        params = {node.attrib['NAME']:node.attrib['VALUE'] for node in tree.iter('PARAMETER') if 'VALUE' in node.attrib}
        self.assertEqual(params['PCW_FCLK0_PERIPHERAL_CLKSRC'], 'IO PLL')
        actual = boot['ps_ref_hz'] * boot['io_pll_fdiv'] / int(params['PCW_FCLK0_PERIPHERAL_DIVISOR0']) / int(params['PCW_FCLK0_PERIPHERAL_DIVISOR1'])
        self.assertAlmostEqual(actual, boot['target_fclk0_hz'], delta=boot['target_fclk0_hz'] * 0.001)

    def sample(self):
        profile = load_profile()
        pose = tuple(profile['start'])
        return Sample(profile_crc(profile), 0, RUNNING, 0, 0xffffffff, 0xffffffff,
                      pose, pose, pose, pose, (0,)*14, (0,)*14)

    def test_request_and_response_preserve_identity_and_hardware_values(self):
        for op, payload in [(wire.BEGIN, wire.MODE.pack(wire.PERIODIC)),
                            (wire.ALLOW, self.sample().pack()), (wire.HOLD, self.sample().pack()),
                            (wire.PAUSE, b''), (wire.RESUME, b''), (wire.STATUS, b''), (wire.END, b'')]:
            request = wire.request(op, 10, 11, 12, payload)
            self.assertEqual(wire.unpack(request), (op, 10, 11, 12, payload))
            hardware = dict(build_id=0x46504732, status=7, timeout_count=3, state=wire.ACTIVE,
                            age_cycles=2**35+1, last_gap_cycles=2**36+2, violations=1, events=8, phase=3)
            self.assertEqual(wire.read_response(wire.response(op,10,11,12,hardware)),
                             (op,10,11,12,hardware))

    def test_corruption_truncation_wrong_version_operation_and_lengths_reject(self):
        valid = wire.request(wire.STATUS, 1, 0, 10)
        candidates = [b'', valid[:10], valid[:-1], valid+b'x']
        corrupt = bytearray(valid)
        corrupt[-1] ^= 1
        candidates.append(bytes(corrupt))
        for offset, value in [(0,0), (4,2), (5,0), (5,0x86), (6,1)]:
            invalid = bytearray(valid[:-4])
            invalid[offset] = value
            candidates.append(bytes(invalid)+struct.pack('<I',zlib.crc32(invalid)&0xffffffff))
        for data in candidates:
            with self.subTest(data=data), self.assertRaises(ValueError):
                wire.unpack(data)
        with self.assertRaises(ValueError):
            wire.read_response(valid)

    def test_invalid_mode_sample_flags_and_operation_payloads_reject(self):
        with self.assertRaises(ValueError):
            wire.request(wire.BEGIN,1,0,1,wire.MODE.pack(3))
        for op, payload in [(wire.BEGIN,b''), (wire.STATUS,b'x'), (wire.ALLOW,b''), (0,b'')]:
            with self.assertRaises(ValueError):
                wire.request(op,1,0,1,payload)
        payload=bytearray(self.sample().pack())
        struct.pack_into('<I',payload,8,16)
        with self.assertRaises(ValueError):
            wire.request(wire.ALLOW,1,0,1,bytes(payload))

    def test_counter_read_retries_a_low_word_rollover(self):
        class Counter:
            def __init__(self):
                self.high = iter((0, 1, 1, 1))
                self.low = iter((0xffffffff, 3))

            def read(self, offset):
                return next(self.high if offset == 4 else self.low)

        self.assertEqual(read_u64(Counter(), 0, 4), (1 << 32) + 3)

    def test_counter_read_fails_if_hardware_never_stabilizes(self):
        class Counter:
            value = 0

            def read(self, offset):
                self.value += 1
                return self.value

        with self.assertRaises(RuntimeError):
            read_u64(Counter(), 0, 4)


if __name__ == '__main__':
    unittest.main()
