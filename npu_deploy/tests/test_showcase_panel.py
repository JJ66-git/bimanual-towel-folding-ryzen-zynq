"""PTY/gate/stop tests use synthetic children only, never robot commands."""
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'npu_deploy'))
from showcase_runner import Session


class PanelRunnerTests(unittest.TestCase):
    def test_authority_denial_cannot_become_success_on_zero_process_exit(self):
        import json
        import importlib.util
        path=ROOT/'fpga_integration/fpga_code/fpga_workspace/host/action_status.py'
        spec=importlib.util.spec_from_file_location('panel_status_fixture',path)
        wire=importlib.util.module_from_spec(spec);spec.loader.exec_module(wire)
        row=wire.values('denied',sink='memory',target_sequence=1,reason='no_matching_reply')
        output='FPGA_AUTHORITY_DISPLAY='+json.dumps(dict(session=42,status=row))
        job=self.job('print('+repr(output)+',flush=True)\n');job.start()
        self.assertTrue(job.finished.wait(4));self.assertEqual(job.snapshot().returncode,0)
        self.assertTrue(job.snapshot().authority_fault);self.assertEqual(job.snapshot().state,'failed')
        # Malformed, old-target and late pass logs do not replace the refusal.
        job._parse_line('FPGA_AUTHORITY_DISPLAY='+json.dumps(dict(session=42,status=dict(row,stage='allowed',mask=0))))
        self.assertEqual(job.snapshot().authority_status,row)
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.jobs=[]
    def tearDown(self):
        for job in self.jobs:
            if job.process and job.process.poll() is None:
                job.stop();job.finished.wait(3)
                if job.process.poll() is None:os.killpg(job.process.pid,signal.SIGKILL)
            if job.worker:job.worker.join(timeout=2)
        self.tmp.cleanup()
    def job(self,source=None,workflow='start'):
        if source is None:command=[sys.executable,str(ROOT/'npu_deploy/showcase_panel_demo.py'),workflow]
        else:
            script=Path(self.tmp.name)/('fixture'+str(len(self.jobs))+'.py');script.write_text(source)
            command=[sys.executable,str(script)]
        job=Session(workflow,command=command,output_dir=Path(self.tmp.name)/('run'+str(len(self.jobs))))
        self.jobs.append(job);return job
    def until(self,predicate):
        end=time.monotonic()+5
        while time.monotonic()<end:
            if predicate():return
            time.sleep(.01)
        self.fail('Fixture timed out')
    def test_operator_must_confirm_every_gate_and_double_click_is_not_queued(self):
        job=self.job();job.start()
        for name in ('platform','fpga','reset','run'):
            self.until(lambda:job.snapshot().gate==name)
            status=job.snapshot()
            self.assertTrue(job.confirm(status.generation))
            self.assertFalse(job.confirm(status.generation))
        self.assertTrue(job.finished.wait(4))
        self.assertEqual(job.snapshot().returncode,0)
        self.assertTrue(job.snapshot().rocm_verified)
        self.assertTrue(job.snapshot().npu_inference)
        self.assertEqual(job.snapshot().stages_done,['platform','fpga','reset','run','return'])
        self.assertTrue(job.snapshot().returned_home)
    def test_stop_at_motion_gate_does_not_enter_reset(self):
        job=self.job();job.start()
        for name in ('platform','fpga'):
            self.until(lambda:job.snapshot().gate==name);job.confirm(job.snapshot().generation)
        self.until(lambda:job.snapshot().gate=='reset');job.stop()
        self.assertTrue(job.finished.wait(3))
        self.assertNotIn('SIMULATION_STAGE_COMPLETE=reset',job.log_path.read_text())
        self.assertEqual(job.snapshot().state,'stopped')
        self.assertFalse(job.confirm(job.snapshot().generation))
    def test_stop_reaches_running_foreground_group(self):
        job=self.job("import signal,time,sys\nsignal.signal(signal.SIGINT,lambda *a:sys.exit(130))\nprint('READY',flush=True)\ntime.sleep(30)\n")
        job.start();self.until(lambda:'READY' in job.lines);job.stop()
        self.assertTrue(job.finished.wait(3));self.assertEqual(job.snapshot().returncode,130)
    def test_log_open_failure_stops_child_and_is_not_reported_as_success(self):
        job=self.job("import time\nprint('READY',flush=True)\ntime.sleep(30)\n")
        job.log_path=job.output_dir  # A directory cannot be opened as console.log.
        job.start()
        self.assertTrue(job.finished.wait(4))
        self.assertEqual(job.snapshot().state,'io_error')
        self.assertNotEqual(job.snapshot().returncode,0)
        self.assertIsNotNone(job.process.poll())

    def test_bad_token_and_post_stop_markers_do_not_enable_buttons(self):
        job=self.job()
        job._parse_line('FLEXIFOLD_PANEL:wrong:GATE:reset')
        self.assertIsNone(job.snapshot().gate)
        job.stop_requested=True
        job._parse_line('FLEXIFOLD_PANEL:'+job.token+':GATE:reset')
        self.assertIsNone(job.snapshot().gate)
    def test_npu_identification_or_partition_is_not_inference(self):
        job=self.job()
        job._parse_line('| [0] | NPU Strix | aie2p |')
        job._parse_line('Actually running on NPU 0')
        self.assertFalse(job.snapshot().npu_partition)
        self.assertFalse(job.snapshot().npu_inference)
        job._parse_line('Actually running on NPU 1')
        self.assertTrue(job.snapshot().npu_partition)
        self.assertFalse(job.snapshot().npu_inference)
        job._parse_line('NPU_FIRST_INFERENCE_COMPLETED: shape=(1,64,960)')
        self.assertTrue(job.snapshot().npu_inference)

    def test_runtime_end_does_not_imply_operator_success(self):
        job=self.job()
        for invalid in ('[]','null','{"origin":"HOST","address":["192.168.2.99",9009],"session":true,"phase":"ENDED"}'):
            job._parse_line('FPGA_TASK_END='+invalid)
            self.assertIsNone(job.snapshot().fpga_task_end)
        job._parse_line('FPGA_TASK_END={"origin":"HOST","address":["192.168.2.99",9009],"session":42,"phase":"ENDED","delivered":true}')
        self.assertEqual(job.snapshot().fpga_task_end['session'],42)
        self.assertIsNone(job.snapshot().operator_outcome)

    def test_interaction_controls_are_large_and_fit_the_supported_window_sizes(self):
        from showcase_panel import render_panel,interaction_controls_layout
        from showcase_runner import Status
        for width,height in ((1000,940),(1520,940),(1920,1080)):
            _,geo=render_panel(Status('interact'),width,height)
            left,top,available,space=geo['interaction'];layout=interaction_controls_layout(available)
            for key,(x,y,w,h) in layout.items():
                self.assertGreaterEqual(x,0);self.assertLessEqual(x+w,available)
                self.assertGreaterEqual(y,0);self.assertLessEqual(y+h,space)
                if key in ('challenge','safe','capture','save'):self.assertGreaterEqual(h,44)
            self.assertGreaterEqual(layout['slider'][3],100)
            self.assertLessEqual(layout['slider'][1]+layout['slider'][3],layout['range'][1])
            self.assertLessEqual(layout['range'][1]+layout['range'][3],layout['challenge'][1])
            self.assertLessEqual(geo['console'][2],left)
            self.assertLessEqual(layout['challenge'][0]+layout['challenge'][2],layout['safe'][0])
            self.assertLessEqual(layout['leader'][1]+layout['leader'][3],layout['capture'][1])
            self.assertLessEqual(layout['capture'][0]+layout['capture'][2],layout['save'][0])

    def test_interaction_controls_require_the_owned_verification_marker(self):
        job=self.job(workflow='interact')
        job._parse_line('http://127.0.0.1:8766/')
        self.assertFalse(job.snapshot().interaction_verified)
        job._parse_line('BOOTH_B35_READY: real PL boundary checks and LCD state matched')
        self.assertTrue(job.snapshot().interaction_verified)
        self.assertFalse(job.snapshot().interaction_server_ready)
        job._parse_line('http://127.0.0.1:8766/ · FPGA UDP')
        self.assertTrue(job.snapshot().interaction_server_ready)
    def test_password_prompt_is_masked_and_not_saved_in_output(self):
        source="""import termios,sys
fd=sys.stdin.fileno();state=termios.tcgetattr(fd);state[3]&=~termios.ECHO;termios.tcsetattr(fd,termios.TCSANOW,state)
print('AUTH_REQUIRED',flush=True)
value=sys.stdin.readline().strip()
print('AUTH_OK' if value=='test-secret-value' else 'AUTH_FAILED',flush=True)
"""
        job=self.job(source);job.start();self.until(lambda:job.snapshot().secret_input)
        self.assertFalse(job.authenticate('bad\ninput'))
        self.assertTrue(job.authenticate('test-secret-value'))
        self.assertTrue(job.finished.wait(3))
        log=job.log_path.read_text();self.assertIn('AUTH_OK',log);self.assertNotIn('test-secret-value',log)
    def test_real_terminal_echo_and_canonical_mode_are_restored(self):
        import pty,termios
        from unittest.mock import patch
        from showcase_runner import TerminalBridge
        job=self.job();job.start();self.until(lambda:job.snapshot().gate=='platform')
        master,slave=pty.openpty();saved=termios.tcgetattr(slave)
        terminal=os.fdopen(os.dup(slave),'r');bridge=TerminalBridge(job)
        try:
            with patch.object(sys,'stdin',terminal):bridge.start()
            self.assertFalse(termios.tcgetattr(slave)[3]&termios.ECHO)
            os.write(master,b'\n')
            self.until(lambda:job.snapshot().stage=='platform')
            bridge.close()
            self.assertEqual(termios.tcgetattr(slave),saved)
        finally:
            bridge.close();terminal.close();os.close(master);os.close(slave)

    def test_password_prompt_through_pipeline_uses_controlling_terminal(self):
        # Like sudo under prepare's tee pipeline: prompt/read through /dev/tty,
        # without a newline. No actual privileges or hardware are involved.
        source='''import os,subprocess,sys,termios
if '--auth' in sys.argv:
    with open('/dev/tty', 'rb', buffering=0) as tty:
        fd=tty.fileno();saved=termios.tcgetattr(fd);hidden=saved.copy()
        hidden[3]&=~termios.ECHO
        termios.tcsetattr(fd,termios.TCSANOW,hidden)
        try:
            os.write(1,b'[sudo] password for fixture: ')
            value=tty.readline().strip()
        finally:
            termios.tcsetattr(fd,termios.TCSANOW,saved)
    print('AUTH_OK' if value==b'test-secret-value' else 'AUTH_FAILED',flush=True)
else:
    subprocess.run(['bash','-o','pipefail','-c',
        '"$1" "$2" --auth 2>&1 | tee "$3"',
        'fixture',sys.executable,__file__,__file__+'.log'],check=True)
'''
        job=self.job(source,workflow='prepare');job.start()
        self.until(lambda:job.snapshot().secret_input)
        self.assertTrue(job.authenticate('test-secret-value'))
        self.assertTrue(job.finished.wait(3))
        self.assertEqual(job.snapshot().returncode,0)
        self.assertIn('AUTH_OK',job.log_path.read_text())
        self.assertNotIn('test-secret-value',job.log_path.read_text())

    def test_log_buffer_is_bounded_and_whole_log_is_saved(self):
        job=self.job("print('\\n'.join('row'+str(i) for i in range(3000)))\n")
        job.start();self.assertTrue(job.finished.wait(4))
        self.assertLessEqual(len(job.lines),1200)
        self.assertLessEqual(len(job.drain()),300)
        self.assertIn('row0',job.log_path.read_text())
        self.assertIn('row2999',job.log_path.read_text())
        self.assertGreater(job.snapshot().dropped_lines,0)


class OriginalScriptPanelTests(unittest.TestCase):
    def test_original_script_still_waits_at_each_motion_confirmation(self):
        import test_showcase_orchestration as fixtures
        fixture=fixtures.ShowcaseOrchestrationTests();fixture.setUp()
        job=Session('start',command=['bash',str(fixture.npu/'start_showcase.sh')],environment=fixture.env,output_dir=fixture.root/'panel-run')
        try:
            job.start()
            for key in ('platform','fpga'):
                end=time.monotonic()+6
                while job.snapshot().gate!=key and not job.finished.is_set() and time.monotonic()<end:time.sleep(.01)
                self.assertEqual(job.snapshot().gate,key)
                self.assertTrue(job.confirm(job.snapshot().generation))
            end=time.monotonic()+6
            while job.snapshot().gate!='reset' and not job.finished.is_set() and time.monotonic()<end:time.sleep(.01)
            self.assertEqual(job.snapshot().gate,'reset')
            self.assertFalse((fixture.root/'reset.called').exists())
            job.stop();self.assertTrue(job.finished.wait(4))
            self.assertFalse((fixture.root/'reset.called').exists())
            self.assertFalse((fixture.root/'hybrid.called').exists())
        finally:
            if job.process and job.process.poll() is None:job.stop();job.finished.wait(4)
            fixture.tearDown()

    def test_gui_unavailable_falls_back_before_any_action(self):
        import subprocess
        import test_showcase_orchestration as fixtures
        fixture=fixtures.ShowcaseOrchestrationTests();fixture.setUp()
        try:
            python=fixture.project/'.venv/bin/python';python.unlink()
            fixture.write(python,'#!/bin/sh\nexit 78\n',executable=True)
            result=subprocess.run(['bash',str(fixture.npu/'start_showcase.sh'),'--panel'],env=fixture.env,
                input='q\n',text=True,capture_output=True,timeout=5)
            self.assertEqual(result.returncode,130)
            self.assertIn('继续使用原终端流程',result.stderr)
            self.assertFalse((fixture.root/'reset.called').exists())
        finally:fixture.tearDown()

if __name__=='__main__':unittest.main()
