import json
import math
import struct
import unittest
import zlib
from unittest.mock import MagicMock,patch

from host import task_status as wire


class TaskProtocolTests(unittest.TestCase):
    def test_explicit_session_is_preserved_and_invalid_sessions_are_rejected(self):
        with patch.object(wire.socket,'socket',return_value=MagicMock()):
            client=wire.Client(('127.0.0.1',9009),session=123)
            self.assertEqual(client.session,123);client.close()
            for session in (0,-1,0xffffffff,True,'123'):
                with self.assertRaises(ValueError):wire.Client(('127.0.0.1',9009),session=session)

    def test_terminal_claims_require_the_real_current_task_session(self):
        from board.fpg1_v3_server import matched_task_claim
        for phase in ('ENDED','FAILED','STOPPED','COMPLETE'):
            for origin in wire.ORIGINS:
                values=dict(phase=phase,activity='IDLE',origin=origin)
                self.assertTrue(matched_task_claim(42,values,(('127.0.0.1',1234),42)))
                for owner in (None,(('127.0.0.1',1234),43)):
                    with self.assertRaisesRegex(ValueError,'task_status_session_mismatch'):matched_task_claim(42,values,owner)
        self.assertFalse(matched_task_claim(42,dict(phase='WAITING',activity='IDLE',origin='HOST'),None))

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
