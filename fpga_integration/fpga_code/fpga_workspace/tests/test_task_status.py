import json
import math
import struct
import unittest
import zlib

from host import task_status as wire


class TaskProtocolTests(unittest.TestCase):
    def test_all_claims_preserve_packet_identity(self):
        for phase in wire.PHASES:
            for activity in wire.ACTIVITIES:
                for origin in wire.ORIGINS:
                    values=dict(phase=phase,activity=activity,origin=origin)
                    for kind in (1,129):
                        self.assertEqual(wire.unpack(wire.pack(kind,42,8,900,values)),
                                         (kind,42,8,900,values))

    def test_corrupt_or_ambiguous_status_is_rejected(self):
        values=dict(phase='FOLD_1',activity='THINKING',origin='HOST')
        valid=wire.pack(1,42,0,10,values)
        corrupt=bytearray(valid);corrupt[-1]^=1
        bad=[b'',valid[:-1],valid+b'x',bytes(corrupt)]
        for offset,value in ((0,0),(4,2),(5,3),(6,255)):
            body=bytearray(valid[:-4]);body[offset]=value
            bad.append(bytes(body)+wire.CRC.pack(zlib.crc32(body)&0xffffffff))
        payloads=[b'[]',b'null',b'\xff',b'{"phase":"FOLD_1","phase":"COMPLETE","activity":"IDLE","origin":"HOST"}',
                  b'{"phase":"COMPLETE","activity":"IDLE","origin":"ROBOT"}',
                  b'{"phase":"COMPLETE","activity":"IDLE","origin":"HOST","confirmed":true}']
        for payload in payloads:
            body=wire.HEADER.pack(wire.MAGIC,1,1,len(payload),1,0,1)+payload
            bad.append(body+wire.CRC.pack(zlib.crc32(body)&0xffffffff))
        for packet in bad:
            with self.subTest(packet=packet),self.assertRaises(ValueError):wire.unpack(packet)
        for timeout in (0,-1,math.inf,math.nan):
            with self.assertRaises(ValueError):wire.Client(('127.0.0.1',1),timeout)


if __name__=='__main__':unittest.main()
