#!/usr/bin/env python3
"""Offline tests only; no devices or network sockets."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'fpga_integration/board_runtime'))
spec = importlib.util.spec_from_file_location('patched_task_monitor', Path(__file__).parent / 'board/task_monitor.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from host import task_status
from host.protocol import frame, REQUEST, RESPONSE


class Journal:
    def __init__(self): self.events = []
    def write(self, event, **values): self.events.append((event, values))


class SessionTests(unittest.TestCase):
    def test_expiry_and_sequence(self):
        lease = module.SessionLease(idle_ns=5)
        a, b = (('192.168.2.10', 1), 101), (('192.168.2.10', 2), 102)
        with self.assertRaises(ValueError): lease.check(a, 1, 9, 0)
        lease.check(a, 0, 9, 0)
        lease.commit(a, 0, 9, 0)
        with self.assertRaises(ValueError): lease.check(a, 0, 9, 5)
        with self.assertRaises(ValueError): lease.check(b, 0, 1, 4)
        with self.assertRaises(ValueError): lease.check(b, 1, 1, 5)
        lease.check(b, 0, 1, 5)
        self.assertTrue(lease.commit(b, 0, 1, 5))
        with self.assertRaises(ValueError): lease.check(a, 1, 10, 10)
        lease.check(b, 1, 2, 11)

    def test_real_task_packets(self):
        journal = Journal()
        inbox = module.Inbox(journal)
        a, b = ('192.168.2.10', 10), ('192.168.2.10', 20)
        values = dict(phase='WAITING', activity='IDLE', origin='HOST')
        first = task_status.pack(1, 123, 0, 100, values)
        second = task_status.pack(1, 456, 0, 200, values)
        self.assertEqual(task_status.unpack(inbox.handle(first, a, 0))[0], 129)
        with self.assertRaises(ValueError): inbox.handle(second, b, 4_000_000_000)
        self.assertEqual(task_status.unpack(inbox.handle(second, b, 5_000_000_000))[1], 456)
        self.assertIn('task_session_replaced_after_idle', [e for e, _ in journal.events])

    def test_fpg1_server_accepts_next_session_after_idle(self):
        import board.task_monitor
        import importlib.util
        sys.modules['board.task_monitor'] = module
        try:
            spec = importlib.util.spec_from_file_location(
                'patched_udp_server', Path(__file__).parent / 'board/pynq_udp_server.py')
            server = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(server)
        finally:
            sys.modules['board.task_monitor'] = board.task_monitor

        a, b = ('192.168.2.10', 10), ('192.168.2.10', 20)
        requests = [(frame(1, 1, 0, 100, bytes(REQUEST.size)), a),
                    (frame(1, 2, 0, 200, bytes(REQUEST.size)), b),
                    (frame(1, 2, 0, 201, bytes(REQUEST.size)), b)]
        journal = Journal()
        journal.running = True
        replies = []

        class FakeSocket:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def bind(self, *args): pass
            def settimeout(self, *args): pass
            def recvfrom(self, *args):
                if requests:
                    return requests.pop(0)
                journal.running = False
                raise server.socket.timeout()
            def sendto(self, data, address):
                replies.append((data, address))
                return len(data)

        class FakeMonitor:
            def __init__(self, *args): pass
            def poll(self): pass
            def close(self): pass

        def response(packet, device, profile):
            _, session, sequence, tick, _ = server.unframe(packet)
            return frame(2, session, sequence, tick, RESPONSE.pack(0, 0, 0, 0, 1))

        with patch.object(server.socket, 'socket', return_value=FakeSocket()), \
             patch.object(server.signal, 'signal'), \
             patch.object(server.time, 'monotonic_ns', side_effect=[0, 1_000_000_000, 6_000_000_000]), \
             patch.object(server, 'Monitor', FakeMonitor), \
             patch.object(server, 'serve_packet', side_effect=response):
            server.serve(None, None, journal, '0.0.0.0', 9009, '192.168.2.10')
        self.assertEqual([address for _, address in replies], [a, b])
        events = [event for event, _ in journal.events]
        self.assertIn('packet_rejected', events)
        self.assertIn('check_session_replaced_after_idle', events)

if __name__ == '__main__': unittest.main()
