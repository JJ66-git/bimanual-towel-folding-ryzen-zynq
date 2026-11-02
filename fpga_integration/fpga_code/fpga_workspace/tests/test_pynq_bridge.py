import struct
import unittest

from board.ps_uio_server import GuardUIO
from board.pynq_udp_server import BUILD_ID, PynqGuard, serve_packet
from host.protocol import RESPONSE, RUNNING, Sample, frame, unframe
from host.reference import load_profile, profile_crc


class FakeIP:
    def __init__(self):
        self.memory = bytearray(4096)
        struct.pack_into("<I", self.memory, 0x00, BUILD_ID)
        self.writes = []

    def read(self, offset):
        return struct.unpack_from("<I", self.memory, offset)[0]

    def write(self, offset, value):
        self.writes.append((offset, value))
        struct.pack_into("<I", self.memory, offset, value)
        if offset == 0x04:
            struct.pack_into("<I", self.memory, 0x08, 0x2)
            struct.pack_into("<I", self.memory, 0x40, 0x1234)
            struct.pack_into("<I", self.memory, 0x44, 0x5678)
            struct.pack_into("<I", self.memory, 0x48, 4)


class PynqBridgeTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile()
        pose = tuple(self.profile["start"])
        self.sample = Sample(profile_crc(self.profile), 42, RUNNING, 10,
                             0xFFFFFFFF, 0xFFFFFFFF, pose, pose, pose, pose,
                             (0,) * 14, (0,) * 14)

    def test_pynq_mmio_register_windows_and_decision(self):
        ip = FakeIP()
        device = PynqGuard(ip, timeout=0.01)
        self.assertEqual(device.check(self.sample), (0x1234, 0x5678, 4))
        self.assertIn((0x10, self.sample.profile_crc), ip.writes)
        self.assertIn((0x14, self.sample.step), ip.writes)
        self.assertIn((0x100, self.sample.target[0]), ip.writes)
        self.assertIn((0x140, self.sample.measured[0]), ip.writes)
        self.assertIn((0x180, self.sample.previous[0]), ip.writes)
        self.assertIn((0x1C0, self.sample.act[0]), ip.writes)
        self.assertIn((0x200, self.sample.correction[0]), ip.writes)
        self.assertIn((0x240, self.sample.previous_correction[0]), ip.writes)

    def test_udp_frame_metadata_and_device_result(self):
        class Device:
            def check(self, sample):
                self.received = sample
                return 7, 8, 4

        device = Device()
        request = frame(1, 12, 0, 567, self.sample.pack())
        reply = serve_packet(request, device, self.profile)
        kind, session, sequence, tick, payload = unframe(reply)
        self.assertEqual((kind, session, sequence, tick), (2, 12, 0, 567))
        self.assertEqual(device.received, self.sample)
        self.assertEqual(RESPONSE.unpack(payload)[:4], (profile_crc(self.profile), 7, 8, 4))

    def test_profile_mismatch_is_rejected_before_mmio(self):
        from dataclasses import replace

        class Device:
            def check(self, sample):
                raise AssertionError("Must not touch PL")

        request = frame(1, 12, 0, 567, replace(self.sample, profile_crc=0).pack())
        with self.assertRaises(ValueError):
            serve_packet(request, Device(), self.profile)

    def test_board_bridge_timeouts_must_be_finite_and_positive(self):
        for timeout in (float("nan"), float("inf"), float("-inf"), 0):
            with self.assertRaises(ValueError):
                PynqGuard(None, timeout=timeout)
            with self.assertRaises(ValueError):
                GuardUIO("missing-uio-device", timeout=timeout)


if __name__ == "__main__":
    unittest.main()
