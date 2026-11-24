"""Exercise the actual server loop while the independent LCD is not ready."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from board.fpg1_v3_server import serve
from board.fold_bridge import FoldSession
from host import fold_protocol as wire
from host.protocol import Sample,UNKNOWN
from host.reference import load_profile,profile_crc,evaluate


class StartupTests(unittest.TestCase):
    def test_udp_guard_replies_while_lcd_initialization_is_pending(self):
        profile=load_profile();crc=profile_crc(profile);pose=tuple(profile['start']);zero=(0,)*14
        sample=Sample(crc,UNKNOWN,0,0,UNKNOWN,UNKNOWN,pose,pose,pose,pose,zero,zero)
        packet=wire.encode(b'FPG1',3,1,123,0,1,wire.FPG1_REQUEST.unpack(sample.pack()))
        sent=[];events=[]
        class Guard:
            guard_crc=crc;fold_identity={};fold_profile={'calibration_status':'synthetic_test'}
            def read(self,offset):return 0
            def check(self,candidate,*args,**kwargs):
                ref=evaluate(candidate,profile)
                return wire.FPG1_REPLY.pack(crc,ref['violations'],ref['events'],ref['phase'],1,0,4,15,wire.BUILD_ID,UNKNOWN)
        class Socket:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def bind(self,address):pass
            def settimeout(self,value):pass
            def recvfrom(self,size):return packet,('127.0.0.1',12345)
            def sendto(self,response,address):sent.append(response);return len(response)
        class Journal:
            @property
            def running(self):return not sent
            def write(self,event,**fields):events.append(event)
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal();journal.output=SimpleNamespace(name=str(Path(directory)/'server.jsonl'))
            display=SimpleNamespace(ready=False,error=None,accepted=lambda *args:None,update=lambda **kwargs:None)
            with patch('board.fpg1_v3_server.socket.socket',return_value=Socket()):
                serve(FoldSession('127.0.0.1',Guard(),profile),journal,'127.0.0.1',9009,'127.0.0.1',display)
            self.assertEqual(wire.decode(sent[0]).session,123)
            health=json.loads((Path(directory)/'service_health.json').read_text())
            self.assertTrue(health['udp_bound']);self.assertFalse(health['display_ready'])
            self.assertIn('fold_server_started',events);self.assertIn('fold_reply_sent',events)


if __name__=='__main__':unittest.main()
