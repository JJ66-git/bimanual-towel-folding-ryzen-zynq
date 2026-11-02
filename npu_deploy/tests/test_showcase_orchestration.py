"""Exercise script sequencing using only temporary, simulated commands."""
import os
import signal
import time
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ShowcaseOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.npu = self.root/'npu_deploy'
        self.project = self.root/'amd_hybrid_deploy_20261031'
        self.bin = self.root/'bin'
        for p in (self.npu, self.project/'.venv/bin', self.bin): p.mkdir(parents=True)
        (self.npu/'start_showcase.sh').write_text((ROOT/'npu_deploy/start_showcase.sh').read_text())
        self.write(self.npu/'field_budget.py', 'print("FIELD_BUDGET mock 750")\n')
        self.write(self.npu/'fpga_service_probe.py', 'import os,sys\nopen(os.environ["TEST_PROBE_MARKER"],"w").write("probe")\nprint("FPGA_SERVICE_ACK mock")\nsys.exit(int(os.environ.get("MOCK_PROBE_STATUS", "0")))\n')
        self.write(self.npu/'demo_rocm_compute.py', 'print("ROCM_COMPUTE mock")\n')
        (self.project/'.venv/bin/python').symlink_to('/usr/bin/python3')
        self.write(self.project/'.venv/bin/amd-smi', '#!/bin/sh\necho AMD-SMI-mock\n', executable=True)
        self.write(self.bin/'sudo', '#!/bin/sh\nexit 0\n', executable=True)
        self.write(self.bin/'sleep', '#!/bin/sh\nexit 0\n', executable=True)
        self.write(self.bin/'lscpu', '#!/bin/sh\necho "Model name: AMD mock"\n', executable=True)
        self.write(self.project/'run.sh', '''#!/bin/bash
printf reset > "$TEST_RESET_MARKER"
case "${MOCK_RESET:-ok}" in
  ok) echo RESET_EXECUTION_COMPLETE; exit 0 ;;
  cancel) echo Cancelled; exit 0 ;;
  interrupt) exit 130 ;;
esac
''', executable=True)
        self.write(self.npu/'run_hybrid_npu_fpga.sh', '''#!/bin/bash
printf '%s\\n' "$*" "${AIPC_FPGA_REQUIRE_START:-missing}" > "$TEST_HYBRID_MARKER"
''', executable=True)
        self.bash_env=self.root/'bash_env'
        self.bash_env.write_text("xrt-smi() { printf '%s\\n' '| [0] | NPU mock |'; }\n")
        self.env=os.environ.copy()
        self.env.update(PATH=str(self.bin)+':'+self.env.get('PATH','/usr/bin:/bin'),
            BASH_ENV=str(self.bash_env), TEST_RESET_MARKER=str(self.root/'reset.called'),
            TEST_HYBRID_MARKER=str(self.root/'hybrid.called'), TEST_PROBE_MARKER=str(self.root/'probe.called'),
            AIPC_FPGA_SHADOW='192.0.2.1:9009')

    def tearDown(self): self.tmp.cleanup()

    @staticmethod
    def write(path, text, executable=False):
        path.write_text(text)
        if executable: path.chmod(0o755)

    def run_script(self, user_input='\n\n\n\n', **env):
        return subprocess.run(['bash',str(self.npu/'start_showcase.sh')],env={**self.env,**env},
                              input=user_input,capture_output=True,text=True,timeout=15)

    def test_success_starts_exact_budget_and_requires_fpga_probe(self):
        result=self.run_script()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        text=(self.root/'hybrid.called').read_text()
        self.assertIn('--steps 750 --execute --trace',text)
        self.assertIn('--preview',text)
        self.assertTrue(text.rstrip().endswith('1'))

    def test_cancelled_reset_never_starts_hybrid(self):
        result=self.run_script(MOCK_RESET='cancel')
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.root/'hybrid.called').exists())

    def test_interrupted_reset_never_starts_hybrid(self):
        result=self.run_script(MOCK_RESET='interrupt')
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.root/'hybrid.called').exists())

    def test_ctrl_c_at_reset_confirmation_does_not_start_motion(self):
        marker=self.root/'probe.called'
        process=subprocess.Popen(['bash',str(self.npu/'start_showcase.sh')],env=self.env,
                                 stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                 text=True,start_new_session=True)
        try:
            process.stdin.write('\n\n')
            process.stdin.flush()
            deadline=time.monotonic()+8
            while not marker.exists() and process.poll() is None and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertTrue(marker.exists(), 'script did not reach FPGA probe')
            # Keep stdin open without confirming reset and interrupt the whole foreground group.
            time.sleep(.1)
            os.killpg(process.pid,signal.SIGINT)
            out,err=process.communicate(timeout=5)
            self.assertEqual(process.returncode,130,out+err)
            self.assertFalse((self.root/'reset.called').exists())
            self.assertFalse((self.root/'hybrid.called').exists())
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGKILL)
                process.communicate(timeout=5)

    def test_ctrl_c_during_countdown_does_not_start_reset(self):
        marker = self.root/'sleep.started'
        self.write(self.bin/'sleep', '#!/bin/sh\ntouch "$TEST_SLEEP_MARKER"\nexec /usr/bin/sleep 30\n', executable=True)
        env={**self.env, 'TEST_SLEEP_MARKER':str(marker)}
        process=subprocess.Popen(['bash',str(self.npu/'start_showcase.sh')],env=env,
                                 stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        try:
            process.stdin.write('\n\n\n')
            process.stdin.flush()
            deadline=time.monotonic()+8
            while not marker.exists() and process.poll() is None and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertTrue(marker.exists(), 'script did not reach countdown')
            os.killpg(process.pid,signal.SIGINT)
            out,err=process.communicate(timeout=5)
            self.assertEqual(process.returncode,130,out+err)
            self.assertFalse((self.root/'reset.called').exists())
            self.assertFalse((self.root/'hybrid.called').exists())
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGKILL)
                process.communicate(timeout=5)

    def test_quit_before_reset_never_starts_motion(self):
        result=self.run_script(user_input='\n\nq\n')
        self.assertEqual(result.returncode,130)
        self.assertFalse((self.root/'reset.called').exists())
        self.assertFalse((self.root/'hybrid.called').exists())

    def test_quit_after_reset_does_not_start_task(self):
        result=self.run_script(user_input='\n\n\nq\n')
        self.assertEqual(result.returncode,130)
        self.assertTrue((self.root/'reset.called').exists())
        self.assertFalse((self.root/'hybrid.called').exists())

    def test_eof_before_reset_does_not_start_motion(self):
        result=self.run_script(user_input='\n\n')
        self.assertEqual(result.returncode,130)
        self.assertFalse((self.root/'reset.called').exists())

    def test_other_input_does_not_confirm(self):
        result=self.run_script(user_input='not-enter\nq\n')
        self.assertEqual(result.returncode,130)
        self.assertIn('请直接按 Enter',result.stdout)
        self.assertFalse((self.root/'reset.called').exists())

    def test_fpga_service_failure_does_not_reset(self):
        result=self.run_script(MOCK_PROBE_STATUS='1')
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.root/'reset.called').exists())
        self.assertFalse((self.root/'hybrid.called').exists())


if __name__ == '__main__': unittest.main()
