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
        (self.npu/'prepare_showcase.sh').write_text((ROOT/'npu_deploy/prepare_showcase.sh').read_text())
        (self.npu/'reset_showcase.sh').write_text((ROOT/'npu_deploy/reset_showcase.sh').read_text())
        self.write(self.npu/'npu_preflight_reuse.py', 'print("NPU_PREFLIGHT_REUSED: mock")\n')
        self.write(self.npu/'field_budget.py', 'print("FIELD_BUDGET mock 750")\n')
        self.write(self.npu/'fpga_service_probe.py', 'import os,sys\nopen(os.environ["TEST_PROBE_MARKER"],"w").write("probe")\nprint("FPGA_SERVICE_ACK mock")\nsys.exit(int(os.environ.get("MOCK_PROBE_STATUS", "0")))\n')
        self.write(self.npu/'demo_rocm_compute.py', 'print("ROCM_COMPUTE mock")\n')
        (self.project/'.venv/bin/python').symlink_to('/usr/bin/python3')
        self.write(self.project/'.venv/bin/amd-smi', '#!/bin/sh\necho AMD-SMI-mock\n', executable=True)
        self.write(self.bin/'sudo', '#!/bin/sh\nexit 0\n', executable=True)
        self.write(self.bin/'sleep', '#!/bin/sh\nexit 0\n', executable=True)
        self.write(self.bin/'lscpu', '#!/bin/sh\necho "Model name: AMD mock"\n', executable=True)
        self.write(self.project/'run.sh', '''#!/bin/bash
if [[ -f "$TEST_RESET_MARKER" ]]; then
  reset_result="${MOCK_RETURN:-ok}"
else
  reset_result="${MOCK_RESET:-ok}"
fi
printf 'reset\n' >> "$TEST_RESET_MARKER"
case "$reset_result" in
  ok) echo RESET_EXECUTION_COMPLETE; exit 0 ;;
  cancel) echo Cancelled; exit 0 ;;
  interrupt) exit 130 ;;
esac
''', executable=True)
        self.write(self.npu/'run_hybrid_npu_fpga.sh', '''#!/bin/bash
printf '%s\\n' "$*" "auto_off=${AIPC_FPGA_AUTO_OFF:-missing}" "${AIPC_FPGA_REQUIRE_START:-missing}" > "$TEST_HYBRID_MARKER"
if [[ "${MOCK_FOLD_FINISH:-0}" == 1 ]]; then echo TOWEL_FINISH_HOLD; fi
if [[ "${MOCK_BUDGET_HOLD:-0}" == 1 ]]; then echo TOWEL_BUDGET_HOLD; fi
if [[ "${MOCK_FIRST_FOLD_HOLD:-0}" == 1 ]]; then echo TOWEL_FIRST_FOLD_HOLD; fi
exit "${MOCK_HYBRID_STATUS:-0}"
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
        self.assertIn('--first-grasp-check',text)
        self.assertIn('--finish-check --act-fp16',text)
        self.assertTrue(text.rstrip().endswith('1'))
        self.assertIn('auto_off=0',text)
        self.assertEqual((self.root/'reset.called').read_text().splitlines(), ['reset'])

    def test_completed_fold_runs_native_return_and_checks_feedback(self):
        result=self.run_script(MOCK_FOLD_FINISH='1')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual((self.root/'reset.called').read_text().splitlines(), ['reset','reset'])
        self.assertIn('TOWEL_RETURN_COMPLETE',result.stdout)
        self.assertTrue(any((self.npu/'artifacts/showcase').glob('work_*/return.log')))

    def test_interrupted_task_does_not_return_even_after_hold_marker(self):
        result=self.run_script(MOCK_FOLD_FINISH='1',MOCK_HYBRID_STATUS='130')
        self.assertEqual(result.returncode,130)
        self.assertEqual((self.root/'reset.called').read_text().splitlines(), ['reset'])

    def test_budget_hold_runs_return_after_confirmation(self):
        result=self.run_script('\n\n\n\n\n',MOCK_BUDGET_HOLD='1',AIPC_SHOWCASE_PANEL_TOKEN='fixture')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual((self.root/'reset.called').read_text().splitlines(), ['reset','reset'])
        self.assertLess(result.stdout.index('FLEXIFOLD_PANEL:fixture:GATE:return'),
                        result.stdout.index('FLEXIFOLD_PANEL:fixture:STAGE:return'))
        self.assertIn('TOWEL_RETURN_COMPLETE',result.stdout)

    def test_budget_hold_can_be_left_in_place(self):
        result=self.run_script('\n\n\n\nq\n',MOCK_BUDGET_HOLD='1')
        self.assertEqual(result.returncode,130)
        self.assertEqual((self.root/'reset.called').read_text().splitlines(), ['reset'])
        self.assertNotIn('TOWEL_RETURN_COMPLETE',result.stdout)

    def test_first_fold_retry_requires_confirmation_for_return(self):
        cancelled = self.run_script('\n\n\n\nq\n', MOCK_FIRST_FOLD_HOLD='1',
                                    AIPC_SHOWCASE_PANEL_TOKEN='fixture')
        self.assertEqual(cancelled.returncode, 130)
        self.assertIn('FLEXIFOLD_PANEL:fixture:GATE:return', cancelled.stdout)
        self.assertIn('首抓后毛巾仍摊开', cancelled.stdout)
        self.assertEqual((self.root/'reset.called').read_text().splitlines(), ['reset'])

        (self.root/'reset.called').unlink()
        returned = self.run_script('\n\n\n\n\n', MOCK_FIRST_FOLD_HOLD='1')
        self.assertEqual(returned.returncode, 0, returned.stdout + returned.stderr)
        self.assertEqual((self.root/'reset.called').read_text().splitlines(), ['reset','reset'])
        self.assertIn('TOWEL_RETURN_COMPLETE', returned.stdout)

    def test_return_without_arrival_feedback_is_reported_as_failure(self):
        result=self.run_script(MOCK_FOLD_FINISH='1',MOCK_RETURN='cancel')
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('TOWEL_RETURN_COMPLETE',result.stdout)
        self.assertIn('未收到归位到位反馈',result.stderr)

    def test_unqualified_authority_is_rejected_before_any_reset_or_board_change(self):
        import shutil
        original=ROOT/'amd_hybrid_deploy_20261031/piper_towel_replica/deployment_entry'
        target=self.project/'piper_towel_replica/deployment_entry';target.mkdir(parents=True)
        shutil.copy2(original/'fpga_action_authority.py',target/'fpga_action_authority.py')
        workspace=self.root/'fpga_integration/fpga_code/fpga_workspace';workspace.mkdir(parents=True)
        for name in ('host','config'):
            shutil.copytree(ROOT/'fpga_integration/fpga_code/fpga_workspace'/name,workspace/name,ignore=shutil.ignore_patterns('__pycache__'))
        authority=self.npu/'fpga_authority';authority.mkdir()
        for name in ('check_config.py','required.example.json'):shutil.copy2(ROOT/'npu_deploy/fpga_authority'/name,authority/name)
        result=self.run_script(AIPC_FPGA_AUTHORITY_CONFIG=str(authority/'required.example.json'))
        self.assertNotEqual(result.returncode,0);self.assertIn('not qualified',result.stderr)
        self.assertFalse((self.root/'reset.called').exists());self.assertFalse((self.root/'hybrid.called').exists())
        self.assertFalse((self.root/'probe.called').exists())

    def test_reset_only_runs_without_platform_fpga_or_npu_tools(self):
        (self.npu/'field_budget.py').unlink()
        (self.npu/'npu_preflight_reuse.py').unlink()
        (self.npu/'fpga_service_probe.py').unlink()
        result=subprocess.run(['bash',str(self.npu/'reset_showcase.sh')],env=self.env,
            input='\n',capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('RESET_ONLY_COMPLETE:',result.stdout)
        self.assertFalse((self.root/'probe.called').exists())
        self.assertFalse((self.root/'hybrid.called').exists())

    def test_cancel_reset_only_does_not_move(self):
        result=subprocess.run(['bash',str(self.npu/'reset_showcase.sh')],env=self.env,
            input='q\n',capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,130)
        self.assertFalse((self.root/'reset.called').exists())

    def test_reset_entry_works_without_executable_permission(self):
        (self.project/'run.sh').chmod(0o644)
        result=self.run_script()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue((self.root/'reset.called').exists())
        self.assertTrue((self.root/'hybrid.called').exists())

    def test_prepare_entry_works_without_executable_permission(self):
        self.write(self.project/'run.sh', '#!/bin/bash\n[[ "$1" == prepare ]] || exit 1\necho PREPARE_MOCK_REACHED\n')
        self.write(self.npu/'check_preparation_result.py', 'import sys\nfrom pathlib import Path\nassert "PREPARE_MOCK_REACHED" in Path(sys.argv[1]).read_text()\n')
        self.write(self.bin/'ip', '#!/bin/sh\necho "192.0.2.1 dev test src 192.0.2.2"\n', executable=True)
        self.write(self.bin/'ping', '#!/bin/sh\nexit 0\n', executable=True)
        (self.npu/'fpga_lifecycle').mkdir()
        self.write(self.npu/'fpga_lifecycle/control_client.py', 'print("FPGA_START_MOCK")\n')
        self.write(self.npu/'run_npu_preflight.sh', 'echo NPU_PREFLIGHT_MOCK\n')
        result=subprocess.run(['bash',str(self.npu/'prepare_showcase.sh')],env=self.env,
            input='\n\n\n\n',capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('PREPARATION_COMPLETE:',result.stdout)
        self.assertFalse((self.root/'reset.called').exists())
        self.assertFalse((self.root/'hybrid.called').exists())

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
