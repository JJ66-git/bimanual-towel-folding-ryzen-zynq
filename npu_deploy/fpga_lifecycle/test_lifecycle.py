#!/usr/bin/env python3
import signal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import control_protocol as protocol
import board_manager as board
import control_client as client


class FakeProcess:
    def __init__(self): self.pid=42; self.returncode=None; self.signals=[]
    def poll(self): return self.returncode
    def send_signal(self, sig): self.signals.append(sig)


class LifecycleTests(unittest.TestCase):
    def test_authenticated_packet_and_tamper(self):
        key=bytes(range(32))
        packet=protocol.make_message(key,'request','start')
        protocol.check_message(key,packet,{'request'})
        result=protocol.reply(key,packet,'starting_or_running')
        self.assertEqual(protocol.check_message(key,result,{'reply'})['nonce'],packet['nonce'])
        packet['value']='finish'
        with self.assertRaises(ValueError): protocol.check_message(key,packet,{'request'})

    def test_end_waits_ten_seconds_then_sends_sigint(self):
        now=[100.0]; process=FakeProcess()
        manager=board.Manager(lambda:process, now=lambda:now[0])
        self.assertEqual(manager.handle('start'),'starting_or_running')
        self.assertEqual(manager.handle('finish'),'stop_scheduled_10s')
        now[0]=109.9; manager.update(); self.assertEqual(process.signals,[])
        now[0]=110.0; manager.update(); self.assertEqual(process.signals,[signal.SIGINT])
        self.assertEqual(manager.handle('start'),'stopping')
        process.returncode=0; manager.update()
        self.assertEqual(manager.handle('status'),'stopped')

    def test_new_prepare_cancels_pending_shutdown(self):
        now=[100.0]; process=FakeProcess()
        manager=board.Manager(lambda:process,now=lambda:now[0])
        manager.handle('start'); manager.handle('finish')
        now[0]=105.0; manager.handle('start')
        now[0]=120.0; manager.update()
        self.assertEqual(process.signals,[])
        self.assertEqual(manager.handle('status'),'starting_or_running')

    def test_client_accepts_only_a_matching_signed_reply(self):
        key=bytes(range(32))
        class FakeSocket:
            def __init__(self,*args): self.packet=None
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def settimeout(self,*args): pass
            def connect(self,*args): pass
            def send(self,data): self.packet=json.loads(data)
            def recv(self,*args): return protocol.dumps(protocol.reply(key,self.packet,'stop_scheduled_10s'))
        with tempfile.TemporaryDirectory() as tmp:
            key_path=Path(tmp)/'key'; key_path.write_text(key.hex())
            with patch.object(client,'KEY',key_path),patch.object(client.socket,'socket',FakeSocket):
                self.assertEqual(client.request('finish'),'stop_scheduled_10s')

if __name__=='__main__': unittest.main()
