import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/'npu_deploy/run_hybrid_npu_fpga.sh'


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)
        (self.path/'systemd-run').write_text('#!/bin/bash\necho ready > "$MOCK_READY"\nif [[ "${MOCK_WAIT:-0}" == 1 ]]; then exec /usr/bin/sleep 30; fi\nexit "${MOCK_EXIT:-0}"\n')
        (self.path/'python3').write_text('#!/bin/bash\necho "$*" > "$MOCK_FINISH"\nexit 0\n')
        (self.path/'systemd-run').chmod(0o755); (self.path/'python3').chmod(0o755)
        (self.path/'bash_env').write_text('ulimit() { echo unlimited; }\n')
        self.env={**os.environ,'PATH':str(self.path)+':'+os.environ['PATH'],
                  'BASH_ENV':str(self.path/'bash_env'),'MOCK_READY':str(self.path/'ready'),
                  'MOCK_FINISH':str(self.path/'finish'),'AIPC_FPGA_SHADOW':'192.168.2.99:9009',
                  'AIPC_FPGA_AUTO_OFF':'1'}
    def tearDown(self): self.tmp.cleanup()
    def test_failure_preserves_status_and_finishes(self):
        result=subprocess.run(['bash',str(SCRIPT)],env={**self.env,'MOCK_EXIT':'42'},capture_output=True,text=True)
        self.assertEqual(result.returncode,42,result.stdout+result.stderr)
        self.assertIn('finish --host 192.168.2.99',(self.path/'finish').read_text())
    def test_ctrl_c_requests_finish(self):
        process=subprocess.Popen(['bash',str(SCRIPT)],env={**self.env,'MOCK_WAIT':'1'},start_new_session=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline=time.monotonic()+5
            while not (self.path/'ready').exists() and time.monotonic()<deadline: time.sleep(.01)
            self.assertTrue((self.path/'ready').exists())
            os.killpg(process.pid,signal.SIGINT)
            process.communicate(timeout=5)
            self.assertTrue((self.path/'finish').exists())
            self.assertNotEqual(process.returncode,0)
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGKILL); process.communicate()

if __name__=='__main__': unittest.main()
