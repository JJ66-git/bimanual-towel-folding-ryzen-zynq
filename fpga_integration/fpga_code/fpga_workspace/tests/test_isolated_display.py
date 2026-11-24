"""Real spawned IPC worker, fake LCD: verify isolation without PYNQ or pixels."""
import json
import os
from pathlib import Path
import pickle
import tempfile
import time
from types import SimpleNamespace
import unittest
import zlib
from board.isolated_display import IsolatedDisplay


def fake_worker(receiver,sender,descriptor,directory,mode,initial):
    sender.send(dict(ready=True,pid=os.getpid()))
    while True:
        operation,state=pickle.loads(zlib.decompress(receiver.recv_bytes()))
        (Path(directory)/'fake_worker.json').write_text(json.dumps(dict(pid=os.getpid(),state=state)))
        sender.send(dict(ack=True,pid=os.getpid()))
        if operation=='close':break
    receiver.close();sender.close()


def waiting_worker(receiver,sender,descriptor,directory,mode,initial):
    # Deliberately never announces ready: construction must still return and
    # retain new state while the independent renderer is initializing.
    operation,state=pickle.loads(zlib.decompress(receiver.recv_bytes()))
    (Path(directory)/'waiting_worker.json').write_text(json.dumps(state))
    receiver.close();sender.close()


class IsolatedTests(unittest.TestCase):
    def test_slow_lcd_initialization_does_not_block_protocol_owner_construction(self):
        with tempfile.TemporaryDirectory() as temporary:
            ip=SimpleNamespace(mmio=SimpleNamespace(base_addr=0x40000000,length=0x80000))
            display=IsolatedDisplay(ip,temporary,'host_observation',worker_target=waiting_worker)
            self.assertFalse(display.ready);self.assertIsNone(display.error)
            display.update(revision=123);display.close()
            self.assertEqual(json.loads((Path(temporary)/'waiting_worker.json').read_text())['revision'],123)

    def test_parent_retains_terminal_state_and_final_snapshot_reaches_another_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            ip=SimpleNamespace(mmio=SimpleNamespace(base_addr=0x40000000,length=0x80000))
            display=IsolatedDisplay(ip,temporary,'host_observation',worker_target=fake_worker)
            self.assertNotEqual(display.child_pid,os.getpid())
            display.update(config_matched=True)
            # Model sample counters are retained by the parent even when paints coalesce.
            for i in range(100):display.update(max_actions=750,revision=i)
            display.close()
            result=json.loads((Path(temporary)/'fake_worker.json').read_text())
            self.assertEqual(result['state']['revision'],99)
            self.assertFalse(display.worker.is_alive());self.assertEqual(result['pid'],display.child_pid)
    def test_booth_packets_are_rendered_from_the_actual_matched_reply(self):
        from host import fold_protocol as wire
        with tempfile.TemporaryDirectory() as temporary:
            ip=SimpleNamespace(mmio=SimpleNamespace(base_addr=0x40000000,length=0x80000))
            display=IsolatedDisplay(ip,temporary,'recorded_replay',True,worker_target=fake_worker)
            values=[0]*90;values[1]=201;values[14]=2650000
            request=wire.Frame(b'FPG1',3,1,123,1,9,tuple(values));reply=wire.Frame(b'FPG1',3,2,123,1,9,(1,1,0,0,1,0,0,15,wire.BUILD_ID,0xffffffff))
            display.accepted(request,reply,1,1);display.close()
            result=json.loads((Path(temporary)/'fake_worker.json').read_text())['state']
            self.assertEqual((result['session'],result['sequence'],result['mask'],result['value']),(123,1,1,2.65))
    def test_paint_coalescing_does_not_drop_checks_or_the_terminal_result(self):
        from host import fold_protocol as wire
        from host.protocol import Sample,RUNNING
        from host.reference import load_profile,profile_crc
        profile=load_profile();crc=profile_crc(profile);pose=tuple(profile['start']);zeros=(0,)*14
        with tempfile.TemporaryDirectory() as temporary:
            ip=SimpleNamespace(mmio=SimpleNamespace(base_addr=0x40000000,length=0x80000))
            display=IsolatedDisplay(ip,temporary,'host_observation',worker_target=fake_worker)
            for i in range(200):
                sample=Sample(crc,i,RUNNING,i*33333,12345,3,pose,pose,pose,pose,zeros,zeros)
                request=wire.Frame(b'FPG1',3,1,123,i+1,99,wire.FPG1_REQUEST.unpack(sample.pack()))
                reply=wire.Frame(b'FPG1',3,2,123,i+1,99,(crc,0,0,3,1234,0,4,15,wire.BUILD_ID,0xffffffff))
                display.accepted(request,reply,time.monotonic_ns(),1234.567+i/7)
            display.host_claim(dict(phase='FAILED',activity='ERROR',origin='HOST'),time.monotonic_ns(),123)
            self.assertIsNone(display.error);self.assertEqual(display.model.s['checked_total'],200)
            display.close()
            state=json.loads((Path(temporary)/'fake_worker.json').read_text())['state']
            self.assertEqual(state['checked_total'],200);self.assertEqual(state['headline'],'本轮执行中断')


if __name__=='__main__':unittest.main()
