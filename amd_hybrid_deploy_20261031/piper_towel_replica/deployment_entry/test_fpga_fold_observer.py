import threading
import io
import json
import unittest
import sys
from unittest.mock import MagicMock,patch
from contextlib import redirect_stdout
from pathlib import Path
from queue import Queue

import numpy as np

WORKSPACE = Path(__file__).resolve().parents[3] / "fpga_integration/fpga_code/fpga_workspace"
sys.path.insert(0, str(WORKSPACE))

from fpga_fold_observer import FpgaFoldObserver, _camera_ready_us


class FakeCamera:
    def __init__(self):
        self.frame_lock = threading.Lock()
        self.latest_frame = None
        self.latest_timestamp = None


class FoldObserverCameraTests(unittest.TestCase):
    def test_close_reports_runtime_end_without_claiming_fold_success(self):
        for outcome,phase in [('ended','ENDED'),('error','FAILED'),('stopped','STOPPED')]:
            observer=FpgaFoldObserver.__new__(FpgaFoldObserver)
            observer.stop_event=threading.Event();observer.ready=threading.Event();observer.ready.set()
            observer.worker=MagicMock();observer.worker.is_alive.return_value=False
            observer._started=True;observer.client=MagicMock(session=123);observer.address=('192.168.2.99',9009)
            output=io.StringIO()
            with patch('host.task_status.Client') as factory,redirect_stdout(output):
                observer.close(outcome=outcome)
            factory.assert_called_once_with(observer.address,timeout=.8,session=123)
            factory.return_value.send.assert_called_once_with(phase,'ERROR' if outcome=='error' else 'IDLE','HOST')
            receipt=json.loads(output.getvalue().split('=',1)[1])
            self.assertEqual(receipt['task_result'],'unconfirmed');self.assertTrue(receipt['delivered'])

    def test_consecutive_matched_task_replies_are_logged(self):
        from host import fold_protocol as wire
        from host.protocol import Sample
        observer = FpgaFoldObserver.__new__(FpgaFoldObserver)
        observer.log = io.StringIO()
        observer.client = type('Client', (), {'session': 123})()
        observer.evaluate = lambda sample, profile: dict(violations=0,events=0,phase=0)
        observer.guard_profile = {}
        observer.calibration_status = 'unconfigured'
        observer._channel = None
        observer._run_id = None
        zeros = (0,)*14
        for step in (0,1):
            sample = Sample(1,step,4,step*33333,1000,0,
                            zeros,zeros,zeros,zeros,zeros,zeros)
            response = type('Response', (), {
                'session':123,'sequence':step+1,
                'values':(1,0,0,0,2000,0,0,step,wire.BUILD_ID,0xFFFFFFFF)})()
            observer._publish_sample(sample,response,True,5000)
        records = [json.loads(line) for line in observer.log.getvalue().splitlines()]
        self.assertEqual([r['step'] for r in records],[0,1])
        self.assertTrue(all(r['session']==123 and r['reference_match'] for r in records))
        self.assertTrue(all(r['event']=='task_reply' and r['dispatched'] for r in records))

    def test_observer_lifecycle_log_uses_client_session_by_default(self):
        observer = FpgaFoldObserver.__new__(FpgaFoldObserver)
        observer.log = io.StringIO()
        observer.client = type('Client', (), {'session':456})()
        observer._log('observer_stopped',errors=0,dropped=0)
        record = json.loads(observer.log.getvalue())
        self.assertEqual(record['session'],456)
        self.assertEqual(record['event'],'observer_stopped')
        self.assertEqual(record['errors'],0)

    def test_camera_clock_is_aligned_to_protocol_monotonic_clock(self):
        self.assertEqual(_camera_ready_us(12.5, perf_now=20.0, monotonic_ns=30_000_000_000),
                         22_500_000)

    def test_snapshot_pairs_copy_with_timestamp_and_only_counts_new_frames(self):
        observer = FpgaFoldObserver.__new__(FpgaFoldObserver)
        observer.camera = FakeCamera()
        observer._frame_id = 0
        observer._last_camera_timestamp = None
        observer.camera.latest_frame = __import__("numpy").zeros((2, 3, 3), dtype="uint8")
        observer.camera.latest_timestamp = 12.5

        first = observer._camera_snapshot()
        self.assertEqual(first[0], 1)
        self.assertAlmostEqual(first[2], 12.5)
        self.assertIsNone(observer._camera_snapshot())

        observer.camera.latest_timestamp = 12.6
        second = observer._camera_snapshot()
        self.assertEqual(second[0], 2)
        self.assertAlmostEqual(second[2], 12.6)

    def test_queue_pressure_keeps_latest_samples(self):
        observer = FpgaFoldObserver.__new__(FpgaFoldObserver)
        observer.queue = Queue(maxsize=2)
        observer._put(("sample", 1))
        observer._put(("sample", 2))
        observer._put(("sample", 3))
        self.assertEqual([observer.queue.get_nowait()[1] for _ in range(2)], [2, 3])

    def test_visual_evidence_requires_a_frame_newer_than_event(self):
        observer = FpgaFoldObserver.__new__(FpgaFoldObserver)
        observer.quality = {"segmentation": {}}
        observer._channel = None
        observer._run_id = None
        observer._baseline_area = 100.0
        observer.cloth_features = lambda image, settings: {"area": 50}
        observer.frames = iter([
            (4, 150, 0.15, np.zeros((1, 1, 3), dtype="uint8")),
            (5, 151, 0.151, np.zeros((1, 1, 3), dtype="uint8")),
        ])
        observer._camera_snapshot = lambda: next(observer.frames)
        sent = []
        observer.client = type("Client", (), {
            "send_feature": lambda _, *args: sent.append(args)
        })()

        self.assertFalse(observer._verify(9, 150))
        self.assertFalse(observer._verify(9, 150))  # A sent frame without a terminal reply is still pending.
        self.assertEqual(sent[0][:4], (9, 150, 5, 151))

    def test_probe_uses_guard_profile_integer_units_without_rescaling(self):
        observer = FpgaFoldObserver.__new__(FpgaFoldObserver)
        observer.guard_crc = 123
        observer.UNKNOWN = 0xFFFFFFFF
        observer.Sample = __import__("host.protocol", fromlist=["Sample"]).Sample
        observer.RUNNING = 4
        observer.CHECK_CORRECTION = 2
        observer._probe_sample = None
        observer._started = False
        observer.vision_enabled = False
        observer.worker = type("Worker", (), {"start": lambda _: None})()
        observer.start((21962, 2913, 6193, -158985, 59100, 135610, 0,
                        -40453, 2582, 5739, 10187, 117154, -91755, 6300), 1200.0)
        sample = observer._probe_sample
        self.assertEqual(sample.target[0], 21962)
        self.assertEqual(sample.target[3], -158985)
        self.assertEqual(sample.target[13], 6300)


if __name__ == "__main__":
    unittest.main()
