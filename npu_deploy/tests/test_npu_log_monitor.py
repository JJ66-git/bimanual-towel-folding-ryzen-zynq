import io
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vision_client import NpuLogMonitor


class MonitorTests(unittest.TestCase):
    def run_monitor(self, lines):
        with open(os.devnull, 'wb') as sink:
            monitor = NpuLogMonitor(io.BytesIO(lines), sink.fileno())
            self.assertTrue(monitor.finished.wait(2))
            return monitor.partition_seen.is_set()

    def test_real_npu_partition(self):
        self.assertTrue(self.run_monitor(b'[Vitis AI EP] NPU 491\nActually running on NPU      1\n'))

    def test_cpu_fallback_or_zero_not_accepted(self):
        self.assertFalse(self.run_monitor(b'[Vitis AI EP] CPU 491\nActually running on NPU      0\n'))
        self.assertFalse(self.run_monitor(b'Execution provider registered\n'))


if __name__ == '__main__':
    unittest.main()
