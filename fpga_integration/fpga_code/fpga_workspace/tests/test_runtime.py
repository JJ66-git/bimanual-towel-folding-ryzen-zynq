from dataclasses import replace
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

from host import task_status
from host import watchdog_protocol as wire
from host.protocol import RUNNING,Sample
from host.reference import load_profile,profile_crc
from host.watchdog_demo import Client

ROOT=Path(__file__).resolve().parents[1]


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class RuntimeEndToEnd(unittest.TestCase):
    def wait_for(self,condition,process,console):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            if condition():return
            if process.poll() is not None:self.fail(console.read_text())
            time.sleep(0.02)
        self.fail('runtime condition timeout: '+console.read_text())

    def start(self,directory,extra=None,port=None):
        if port is None:
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',
            PYTHONPATH=str(ROOT/'tests/fixtures/hardware_boundary')+os.pathsep+str(ROOT),
            FPGA_FIXTURE_RECORD=str(directory/'hardware.jsonl'))
        env.update(extra or {})
        console=directory/'console.txt'
        with console.open('w') as stream:
            process=subprocess.Popen([sys.executable,'-B','-m','board.runtime','--peer','127.0.0.1',
                '--bind','127.0.0.1','--port',str(port),'--source','synthetic_test','--out',str(directory/'run')],
                cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT)
        return process,console,port

    def stop(self,process):
        if process.poll() is None:
            process.terminate()
            try:process.wait(5)
            except subprocess.TimeoutExpired:process.kill();process.wait(3)

    def test_real_udp_task_claims_watchdog_and_async_display(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            process,console,port=self.start(directory,{'FPGA_FIXTURE_DISPLAY_DELAY':'0.35'})
            output=(directory/'pc.jsonl').open('w')
            client=Client(('127.0.0.1',port),0.25,output)
            tasks=task_status.Client(('127.0.0.1',port),0.25)
            try:
                self.wait_for(lambda:any(r['event']=='server_started' for r in rows(directory/'run/board.jsonl')),process,console)
                profile=load_profile();pose=tuple(profile['start'])
                sample=Sample(profile_crc(profile),0,RUNNING,0,0xffffffff,0xffffffff,pose,pose,pose,pose,(0,)*14,(0,)*14)
                client.exchange('begin',wire.BEGIN,wire.MODE.pack(wire.PERIODIC))
                for i in range(8):client.exchange('query_'+str(i),wire.STATUS)
                tasks.send('WAITING','THINKING','HOST')
                before=client.exchange('before_action',wire.STATUS)
                self.assertEqual((before['status'],before['timeout_count']),(1,0))
                client.exchange('action',wire.ALLOW,sample.pack())
                tasks.send('GRASPED','EXECUTING','HOST')
                tasks.send('FOLD_1','THINKING','HOST')
                # A THINKING display claim must not silence PERIODIC monitoring.
                time.sleep(0.65)
                expired=client.exchange('quiet',wire.STATUS)
                self.assertEqual((expired['status'],expired['timeout_count']),(7,1))
                client.exchange('recover',wire.ALLOW,replace(sample,step=1).pack())
                client.exchange('pause',wire.PAUSE)
                tasks.send('FOLD_2','THINKING','OPERATOR')
                time.sleep(0.65)
                paused=client.exchange('paused',wire.STATUS)
                self.assertEqual((paused['status']&3,paused['timeout_count']),(0,1))
                tasks.socket.send(wire.MAGIC+b'corrupt')
                bad=task_status.pack(1,tasks.session,0,tasks.tick,dict(phase='COMPLETE',activity='IDLE',origin='HOST'))
                tasks.socket.send(bad);tasks.socket.settimeout(0.06)
                with self.assertRaises(socket.timeout):tasks.socket.recv(4096)
                tasks.send('COMPLETE','IDLE','HOST')
                self.wait_for(lambda:any(r['task_status'] and r['task_status']['phase']=='COMPLETE'
                    and r['watchdog'] and r['watchdog']['state']==wire.PAUSED for r in rows(directory/'run/frames.jsonl')),process,console)
                client.exchange('resume',wire.RESUME)
                client.exchange('last_action',wire.HOLD,replace(sample,step=2).pack())
                client.exchange('end',wire.END)
                process.wait(8)
            finally:
                client.sock.close();tasks.close();output.close();self.stop(process)
            self.assertEqual(process.returncode,0,console.read_text())
            final=json.loads((directory/'run/final_state.json').read_text())
            self.assertEqual(final['task_phase'],'COMPLETE')
            self.assertEqual(final['checked'],3)
            self.assertTrue(final['service_stopped'])
            self.assertEqual(final['display_mode'],'LIVE')
            hardware=rows(directory/'hardware.jsonl')
            commits=[r for r in hardware if r['event']=='guard_write' and r['offset']==0x50]
            self.assertEqual(len(commits),3)
            self.assertTrue(any(r['event']=='display_enable' and r['value']==0 for r in hardware))
            downloads=[r for r in hardware if r['event']=='overlay_download']
            self.assertTrue(downloads[-1]['bitfile'].endswith('own_hybrid_motion_guard_watchdog.bit'))
            exit_data=json.loads((directory/'run/exit.json').read_text())
            self.assertEqual((exit_data['watchdog_control'],exit_data['watchdog_status']),(0,0))
            report=subprocess.run([sys.executable,'-B','-m','host.watchdog_report','--pc',str(directory/'pc.jsonl'),
                '--board',str(directory/'run/board.jsonl'),'--out',str(directory/'report')],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(report.returncode,0,report.stdout+report.stderr)
            exported=subprocess.run([sys.executable,'-B','-m','host.runtime_report',
                '--run',str(directory/'run'),'--out',str(directory/'runtime_report')],
                cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(exported.returncode,0,exported.stdout+exported.stderr)
            summary=json.loads((directory/'runtime_report/summary.json').read_text())
            self.assertEqual(summary['task_claims'],5)
            self.assertEqual(summary['checks'],3)
            self.assertEqual(summary['watchdog_timeout_events'],1)
            self.assertEqual(summary['observed_commit_gap_ms']['count'],1)
            self.assertFalse(summary['production_tick_period_measured'])
            if os.environ.get('FPGA_TEST_ARTIFACTS'):
                destination=Path(os.environ['FPGA_TEST_ARTIFACTS'])/'runtime'
                shutil.copytree(directory,destination,dirs_exist_ok=True)
                (destination/'source.json').write_text(json.dumps(dict(hardware_boundary='software_fixture',robot_commands=False)))

    def test_coordinated_demo_cli_thinks_folds_and_ends_on_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);process,console,port=self.start(directory)
            try:
                self.wait_for(lambda:any(r['event']=='server_started' for r in rows(directory/'run/board.jsonl')),process,console)
                demo=subprocess.run([sys.executable,'-B','-m','host.coop_demo',
                    '--udp','127.0.0.1:'+str(port),'--out',str(directory/'pc')],
                    cwd=ROOT,capture_output=True,text=True,timeout=15)
                self.assertEqual(demo.returncode,0,demo.stdout+demo.stderr)
                process.wait(8)
            finally:self.stop(process)
            self.assertEqual(process.returncode,0,console.read_text())
            summary=json.loads((directory/'pc/summary.json').read_text())
            self.assertTrue(summary['passed'])
            self.assertFalse(summary['robot_commands'])
            frames=rows(directory/'run/frames.jsonl')
            self.assertTrue(any(r['task_status'] and r['task_status']['activity']=='THINKING' for r in frames))
            self.assertTrue(any(r['task_status'] and r['task_status']['phase']=='COMPLETE' for r in frames))
            self.assertTrue(all(not r['watchdog'] or r['watchdog']['status']==0 for r in frames))
            self.assertFalse(any(r['event']=='guard_write' and r['offset']==0x50 for r in rows(directory/'hardware.jsonl')))
            if os.environ.get('FPGA_TEST_ARTIFACTS'):
                destination=Path(os.environ['FPGA_TEST_ARTIFACTS'])/'coordinated_demo'
                shutil.copytree(directory,destination,dirs_exist_ok=True)
                (destination/'source.json').write_text(json.dumps(dict(hardware_boundary='software_fixture',robot_commands=False)))

    def test_second_service_cannot_reprogram_an_owned_pl(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);process,console,port=self.start(directory)
            with (directory/'pc.jsonl').open('w') as output:
                client=Client(('127.0.0.1',port),0.5,output)
                try:
                    self.wait_for(lambda:any(r['event']=='server_started' for r in rows(directory/'run/board.jsonl')),process,console)
                    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',
                        PYTHONPATH=str(ROOT/'tests/fixtures/hardware_boundary')+os.pathsep+str(ROOT),
                        FPGA_FIXTURE_RECORD=str(directory/'second_hardware.jsonl'))
                    second=subprocess.run([sys.executable,'-B','-m','board.pynq_udp_server',
                        '--peer','127.0.0.1','--log',str(directory/'second.jsonl')],
                        cwd=ROOT,env=env,capture_output=True,text=True,timeout=8)
                    self.assertNotEqual(second.returncode,0)
                    self.assertFalse(rows(directory/'second_hardware.jsonl'))
                    client.exchange('begin',wire.BEGIN,wire.MODE.pack(wire.ON_RESULT))
                    client.exchange('end',wire.END);process.wait(8)
                finally:client.sock.close();self.stop(process)
            self.assertEqual(process.returncode,0,console.read_text())

    def test_display_failure_and_occupied_port_restore_baseline(self):
        for failure in ('display','port'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as temporary:
                directory=Path(temporary)
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as held:
                    held.bind(('127.0.0.1',0))
                    process,console,_=self.start(directory,
                        {'FPGA_FIXTURE_DISPLAY_FAIL':'2'} if failure=='display' else None,
                        held.getsockname()[1] if failure=='port' else None)
                    try:process.wait(8)
                    finally:self.stop(process)
                self.assertNotEqual(process.returncode,0)
                exit_data=json.loads((directory/'run/exit.json').read_text())
                self.assertEqual((exit_data['watchdog_control'],exit_data['watchdog_status']),(0,0))
                observed=rows(directory/'run/board.jsonl')
                self.assertEqual(observed[-1]['event'],'server_stopped')
                if failure=='display':self.assertTrue(any(r['event']=='display_failed' for r in observed))


if __name__=='__main__':unittest.main()
