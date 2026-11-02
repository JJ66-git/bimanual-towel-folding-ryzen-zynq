import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WatchdogStreamEndToEnd(unittest.TestCase):
    def test_cli_periodic_and_on_result_lifecycles_with_real_udp_and_jsonl(self):
        for mode in ('periodic', 'on-result'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
                    reservation.bind(('127.0.0.1', 0))
                    port = reservation.getsockname()[1]
                env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                           PYTHONPATH=str(ROOT / 'tests/fixtures/hardware_boundary') + os.pathsep + str(ROOT))
                with (output / 'server_console.txt').open('w') as console:
                    server = subprocess.Popen([sys.executable, '-B', '-m', 'board.pynq_udp_server',
                        '--bind', '127.0.0.1', '--port', str(port), '--peer', '127.0.0.1',
                        '--log', str(output / 'board.jsonl')], cwd=ROOT, env=env,
                        stdout=console, stderr=subprocess.STDOUT)
                    try:
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            if server.poll() is not None:
                                self.fail('Production server exited: ' + (output / 'server_console.txt').read_text())
                            if (output / 'board.jsonl').exists() and 'server_started' in (output / 'board.jsonl').read_text():
                                break
                            time.sleep(0.02)
                        else:
                            self.fail('Production server did not become ready')
                        client = subprocess.run([sys.executable, '-B', '-m', 'host.watchdog_demo',
                            '--udp', '127.0.0.1:' + str(port), '--out', str(output / 'pc'), '--mode', mode],
                            cwd=ROOT, capture_output=True, text=True, timeout=20)
                        self.assertEqual(client.returncode, 0, client.stdout + client.stderr)
                        summary = json.loads((output / 'pc/summary.json').read_text())
                        self.assertTrue(summary['passed'])
                        self.assertEqual(summary['input_source'], 'synthetic_test')
                        pc_rows = [json.loads(line) for line in (output/'pc/events.jsonl').read_text().splitlines()]
                        self.assertTrue(all(row['host_round_trip_us'] > 0 for row in pc_rows
                                            if row['event'] == 'diagnostic_exchange'))
                        rows = [json.loads(line) for line in (output / 'board.jsonl').read_text().splitlines()]
                        expired = [row for row in rows if row['event'] == 'watchdog_expired']
                        self.assertEqual(len(expired), 1 if mode == 'periodic' else 0)
                        actions = [row for row in rows if row['event'] == 'action_observed']
                        self.assertEqual(len(actions), 4 if mode == 'periodic' else 2)
                        self.assertTrue(all(row['commit_written'] == (mode == 'periodic') for row in actions))
                        ended = next(row for row in rows if row['event'] == 'session_ended')
                        self.assertEqual(ended['watchdog']['status'] & 3, 0)
                        report = subprocess.run([sys.executable, '-B', '-m', 'host.watchdog_report',
                            '--pc', str(output / 'pc/events.jsonl'), '--board', str(output / 'board.jsonl'),
                            '--out', str(output / 'report')], cwd=ROOT, capture_output=True, text=True, timeout=5)
                        self.assertEqual(report.returncode, 0, report.stdout + report.stderr)
                        observed = json.loads((output / 'report/summary.json').read_text())
                        expected = json.loads((ROOT / 'tests/fixtures/watchdog_expected.json').read_text(encoding='utf-8-sig'))[mode]
                        self.assertEqual({key: observed[key] for key in expected}, expected)
                        self.assertTrue((output / 'report/events.csv').is_file())
                        if os.environ.get('FPGA_TEST_ARTIFACTS'):
                            saved = Path(os.environ['FPGA_TEST_ARTIFACTS']) / mode
                            saved.mkdir(parents=True, exist_ok=True)
                            for source, destination in [('board.jsonl','board.jsonl'),
                                    ('pc/events.jsonl','pc_events.jsonl'), ('report/events.csv','events.csv'),
                                    ('report/summary.json','summary.json')]:
                                shutil.copy2(output / source, saved / destination)
                            (saved / 'source.json').write_text(json.dumps(dict(
                                hardware_boundary='software_fixture', input_source='synthetic_test')))
                        if mode == 'periodic':
                            drops = [row for row in rows if row['event'] == 'packet_rejected']
                            self.assertGreaterEqual(len(drops), 4)
                    finally:
                        server.terminate()
                        try:
                            server.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            server.kill()
                            server.wait(timeout=3)


if __name__ == '__main__':
    unittest.main()
