import io
import sys
from dataclasses import replace
import struct
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from board.ps_uio_server import GuardUIO, main as ps_uio_main, serve_packet
from host.link import response_matches
from host.protocol import RUNNING, RESPONSE, Sample, frame, unframe
from host.reference import load_profile, profile_crc


class BridgeChecks(unittest.TestCase):
    def setUp(self):
        self.profile=load_profile()
        pose=tuple(self.profile['start'])
        self.sample=Sample(profile_crc(self.profile),42,RUNNING,0,0xffffffff,7,
                          pose,pose,pose,pose,(0,)*14,(0,)*14)

    def test_reply_metadata_and_actual_device_result(self):
        class Device:
            def check(self,sample):
                self.received=sample
                return 7,8,4
        device=Device()
        request=frame(1,12,34,567,self.sample.pack())
        reply=serve_packet(request,device,self.profile)
        self.assertTrue(response_matches(reply,request))
        self.assertEqual(device.received,self.sample)
        self.assertEqual(RESPONSE.unpack(unframe(reply)[-1])[1:4],(7,8,4))

    def test_wrong_profile_is_rejected_before_mmio(self):
        class Device:
            def check(self,sample):
                raise AssertionError('Must not touch hardware')
        request=frame(1,1,0,1,replace(self.sample,profile_crc=0).pack())
        with self.assertRaises(ValueError):
            serve_packet(request,Device(),self.profile)

    def test_six_vectors_are_written_to_distinct_register_windows(self):
        class MemoryDevice(GuardUIO):
            def __init__(self):
                self.memory=bytearray(4096)
                self.timeout=0.1
            def write(self,address,value):
                super().write(address,value)
                if address==4:
                    struct.pack_into('<I',self.memory,8,2)
        device=MemoryDevice()
        sample=replace(self.sample,target=tuple(range(14)),measured=tuple(range(20,34)),
            previous=tuple(range(40,54)),act=tuple(range(60,74)),
            correction=tuple(range(-14,0)),previous_correction=tuple(range(100,114)))
        self.assertEqual(device.check(sample),(0,0,0))
        for base,expected in ((0x100,0),(0x140,20),(0x180,40),(0x1c0,60),(0x200,-14),(0x240,100)):
            self.assertEqual(struct.unpack_from('<i',device.memory,base)[0],expected)
            self.assertEqual(struct.unpack_from('<i',device.memory,base+52)[0],expected+13)
        self.assertEqual(device.read(0x14),42)
        self.assertEqual(device.read(0x20),7)

    def test_uio_cli_rejects_invalid_port_before_device_open(self):
        for port in ('0', '65536'):
            with patch('sys.argv', [
                    'board.ps_uio_server', '--uio', 'missing-device', '--port', port]):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as result:
                        ps_uio_main()
            self.assertEqual(result.exception.code, 2)
