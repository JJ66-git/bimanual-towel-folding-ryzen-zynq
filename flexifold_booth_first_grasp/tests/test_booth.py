"""Run with python3 -B -m unittest discover -s tests -v from the booth directory."""
import importlib.util
import json
from pathlib import Path
import socket
import threading
import time
import unittest
import tempfile
from unittest.mock import patch

from booth import Booth, Handler, ROOT, RIGHT_J2
from host import fold_protocol as wire
from host.protocol import Sample
from host.reference import evaluate
from leader import Leader, COMMANDS, MAX_AGE, isolated_leader_interface

# Frozen, unmodified policy from the project's B34 board/fold_bridge.py.
spec = importlib.util.spec_from_file_location('fold_bridge_fixture', Path(__file__).parent / 'fixtures/fold_bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class ReferenceGuard:
    """Use the actual B34 session policy; replace only the physical PL."""
    def __init__(self, booth):
        self.guard_crc = booth.crc
        self.profile = booth.profile
        self.samples = []

    def check(self, sample, session, sequence, reset_session=False):
        self.samples.append(sample)
        ref = evaluate(sample, self.profile)
        return wire.FPG1_REPLY.pack(self.guard_crc, ref['violations'], ref['events'],
                                   ref['phase'], 1, 0, 0, 1, wire.BUILD_ID, wire.UNKNOWN)


class MemoryDatagram:
    def __init__(self):
        self.queue = []; self.drop = False; self.wrong_reply = False
        self.errors = []
    def connect(self, address): pass
    def settimeout(self, value): pass
    def close(self): pass
    def send(self, packet):
        if self.drop: return len(packet)
        try:
            response = self.policy.handle(packet, ('127.0.0.1', 12345))
            if self.wrong_reply:
                r = wire.decode(response)
                self.queue.append(wire.encode(wire.MAGIC_FPG1, 3, 2, r.session,
                                              r.sequence+100, r.tick_us, r.values))
            self.queue.append(response[:-1] + bytes([response[-1] ^ 1]))  # bad CRC before the real reply
            self.queue.append(response)
        except ValueError as error: self.errors.append(str(error))
        return len(packet)
    def recv(self, size):
        if not self.queue: raise socket.timeout()
        return self.queue.pop(0)


class BoardTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        self.transport = MemoryDatagram()
        with patch('host.board_link.socket.socket', return_value=self.transport):
            self.booth = Booth('127.0.0.1', 9009)
        self.guard = ReferenceGuard(self.booth)
        self.policy = bridge.FoldSession('127.0.0.1', self.guard, self.booth.profile)
        self.transport.policy = self.policy

    def test_fail_boundary_and_original_with_increasing_diagnostic_ticks(self):
        for angle, mask in [(2650, 1), (2550, 1), (2540, 0), (1750, 0)]:
            result = self.booth.result(self.booth.sample_for_angle(angle))
            self.assertEqual(result['mask'], mask)
            self.assertTrue(result['reference_match'])
            self.assertEqual(result['build_id'], wire.BUILD_ID)
        original = self.booth.result(self.booth.base)
        self.assertEqual(original['mask'], 0)
        self.assertEqual(original['value'], 1.798876)
        self.assertEqual(len(self.guard.samples), 6)
        self.assertEqual([r.step for r in self.guard.samples[1:]], list(range(201, 206)))
        self.assertEqual(self.transport.errors, [])

    def test_old_reply_ignored_and_idle_session_reprobed(self):
        self.transport.wrong_reply = True
        first = self.booth.result(self.booth.sample_for_angle(2650))
        self.booth.board_link.last_success -= 6
        self.policy.last_success -= 6
        second = self.booth.result(self.booth.sample_for_angle(2540))
        self.assertNotEqual(first['session'], second['session'])
        self.assertEqual(second['seq'], 1)
        self.assertEqual(second['mask'], 0)
        self.assertEqual(self.transport.errors, [])

    def test_no_reply_is_error_without_reference_fallback(self):
        self.transport.drop = True
        with self.assertRaises(TimeoutError): self.booth.result(self.booth.base)
        with self.assertRaises(TimeoutError): self.booth.result(self.booth.base)


class HttpTests(unittest.TestCase):
    """Run the real HTTP parser/handler over byte streams, with no listening port."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.booth = Booth(None, 9009, data_dir=self.temp.name)
    def tearDown(self):
        self.booth.collector.close(); self.temp.cleanup()

    def request(self, path, value=None, action='check', method='POST'):
        from io import BytesIO
        from types import SimpleNamespace
        data = b'' if value is None else json.dumps(value).encode()
        headers = ('%s %s HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n'
                   'X-FlexiFold-Action: %s\r\nContent-Length: %d\r\n'
                   'Content-Type: application/json\r\nConnection: close\r\n\r\n') % (method,path,action,len(data))
        handler = Handler.__new__(Handler)
        handler.booth = self.booth; handler.directory = str(ROOT)
        handler.rfile = BytesIO(headers.encode()+data); handler.wfile = BytesIO()
        handler.server = SimpleNamespace(server_port=8765)
        handler.client_address = ('127.0.0.1', 12345)
        handler.log_message = lambda *args: None
        handler.handle_one_request()
        head, body = handler.wfile.getvalue().split(b'\r\n\r\n', 1)
        return int(head.split()[1]), body

    def test_assets_and_live_configuration(self):
        for asset in ['/', '/booth.css', '/interaction.js', '/grasp_before.jpg']:
            status, _ = self.request(asset, method='GET')
            self.assertEqual(status, 200)
        status, body = self.request('/mode', method='GET')
        config=json.loads(body)
        self.assertEqual(config['source'], 'reference')
        self.assertEqual(config['protocol'], 'v3')

    def test_validation_and_reference_results(self):
        for angle in [True, 2651, 1740, 2545, '2540', None]:
            status, _ = self.request('/check/angle', {'millirad':angle})
            self.assertEqual(status, 400)
        for angle, mask in [(2650, 1), (2540, 0)]:
            status, body = self.request('/check/angle', {'millirad':angle})
            result=json.loads(body)
            self.assertEqual(status, 200)
            self.assertEqual(result['mask'], mask)
            self.assertEqual(result['source'], 'reference')

    def test_no_hardware_record_and_action_header(self):
        status, _ = self.request('/capture/start', {'instruction':'测试任务','source':'piper_leader_commands'}, action='record')
        self.assertEqual(status, 400)
        status, _ = self.request('/check/original', action='wrong')
        self.assertEqual(status, 403)

    def test_manual_capture_endpoints_write_to_dedicated_folder(self):
        status, body = self.request('/capture/start', {'instruction':'抓住毛巾','source':'synthetic_rehearsal'}, action='record')
        self.assertEqual(status, 200)
        start=json.loads(body)
        status, body=self.request('/capture/status', method='GET')
        self.assertEqual(json.loads(body)['state'],'recording')
        status, _=self.request('/capture/start', {'instruction':'重复','source':'synthetic_rehearsal'}, action='record')
        self.assertEqual(status,409)
        status, body=self.request('/capture/stop', {'episode_id':start['episode_id']}, action='record')
        self.assertEqual(status,200)
        result=json.loads(body)
        self.assertEqual(result['state'],'complete')
        self.assertTrue(Path(result['saved_dir']).is_relative_to(self.temp.name))
        status, body=self.request('/capture/episodes', method='GET')
        self.assertEqual(len(json.loads(body)['episodes']),1)

    def test_integrated_capture_probes_the_isolated_leader_without_rebinding_during_recording(self):
        self.booth = Booth(None, 9009, data_dir=self.temp.name, auto_leader_can=True)
        sample = dict(joint_deg=[1, 2, 3, 4, 5, 6], gripper_mm=10)
        with patch('booth.isolated_leader_interface', return_value='can_demo') as discover, patch('booth.Leader') as leader:
            reader = leader.return_value
            reader.snapshot.return_value = sample
            reader.status.return_value = dict(ready=True, sample=sample)
            _, body = self.request('/mode', method='GET')
            self.assertTrue(json.loads(body)['leader_enabled'])
            discover.assert_not_called()

            _, body = self.request('/leader/status', method='GET')
            self.assertTrue(json.loads(body)['ready'])
            leader.assert_called_once_with('can_demo')
            code, body = self.request('/capture/start', {'instruction':'现场示范','source':'piper_leader_commands'}, action='record')
            self.assertEqual(code, 200)
            episode = json.loads(body)
            self.assertEqual(Path(episode['saved_dir']).parent.name, 'episodes')

            self.request('/leader/status', method='GET')
            discover.assert_called_once()
            reader.close.assert_not_called()
            code, _ = self.request('/capture/stop', {'episode_id':episode['episode_id']}, action='record')
            self.assertEqual(code, 200)


class LeaderTests(unittest.TestCase):
    def leader(self):
        value=Leader.__new__(Leader)
        value.lock=threading.Lock(); value.record_lock=threading.Lock()
        value.closed=threading.Event(); value.error=None; value.latest={}
        return value

    def test_fresh_complete_components_required(self):
        value=self.leader(); now=time.monotonic()
        self.assertIsNone(value.snapshot())
        value.latest={command:(now,(1000,2000) if i<3 else (3000,)) for i,command in enumerate(COMMANDS)}
        self.assertEqual(value.snapshot()['joint_deg'], [1,2,1,2,1,2])
        self.assertEqual(value.snapshot()['gripper_mm'], 3)
        value.latest[COMMANDS[0]]=(now-MAX_AGE-1,(0,0))
        self.assertIsNone(value.snapshot())

    def test_detects_only_a_single_enabled_usb_adapter_beyond_the_execution_arms(self):
        site = dict(robot=dict(left_port='can_left', right_port='can_right'),
                    usb_serials=dict(left='left-serial', right='right-serial'))

        with tempfile.TemporaryDirectory() as directory:
            net = Path(directory)

            def add(name, serial=None, *, device_type='280', flags='0x1'):
                interface = net / name
                interface.mkdir()
                (interface / 'type').write_text(device_type)
                (interface / 'flags').write_text(flags)
                if serial is not None:
                    (interface / 'device').mkdir()
                    (interface / 'device' / 'serial').write_text(serial)
                return interface

            add('can0', 'left-serial')  # A renamed execution arm is still excluded.
            add('can_right', 'right-serial')
            add('vcan0')
            add('eth0', 'ethernet-serial', device_type='1')
            with self.assertRaisesRegex(RuntimeError, '未识别到独立主臂'):
                isolated_leader_interface(site, net)
            leader = add('can4', 'leader-serial', flags='0x0')
            with self.assertRaisesRegex(RuntimeError, '启用 1 Mbps'):
                isolated_leader_interface(site, net)
            (leader / 'flags').write_text('0x1')
            self.assertEqual(isolated_leader_interface(site, net), 'can4')
            add('can5', 'another-serial')
            with self.assertRaisesRegex(RuntimeError, '检测到多个'):
                isolated_leader_interface(site, net)



if __name__ == '__main__': unittest.main()
