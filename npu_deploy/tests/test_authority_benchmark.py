"""Execute benchmark decisions using the real client and an in-memory board."""
from contextlib import redirect_stdout
import importlib.util
import io
import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
import sys
import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('authority_benchmark_subject',ROOT/'npu_deploy/fpga_authority/benchmark.py')
subject=importlib.util.module_from_spec(spec);spec.loader.exec_module(subject)
from board.fold_bridge import FoldSession
from host.fold_client import FoldClient
from host.reference import load_profile,profile_crc,evaluate
from host import fold_protocol as wire


def client_and_profile():
    profile=load_profile();crc=profile_crc(profile)
    class Guard:
        guard_crc=crc
        def check(self,sample,session,sequence,reset_session=False):
            ref=evaluate(sample,profile)
            return wire.FPG1_REPLY.pack(crc,ref['violations'],ref['events'],ref['phase'],10,0,4,15,wire.BUILD_ID,0xffffffff)
    policy=FoldSession('127.0.0.1',Guard(),profile)
    class Socket:
        def __init__(self,*args):self.rows=[]
        def connect(self,address):pass
        def settimeout(self,value):pass
        def close(self):pass
        def send(self,data):self.rows.append(policy.handle(data,('127.0.0.1',30123)))
        def recv(self,size):return self.rows.pop(0)
    client=FoldClient(('127.0.0.1',9009),crc,socket_factory=Socket)
    client.probe(subject.sample(profile,0xffffffff))
    return client,profile


class BenchmarkTests(unittest.TestCase):
    def test_failure_after_latency_cannot_keep_a_partial_pass(self):
        class Board:
            def request(self,*args,**kwargs):pass
            def authenticated(self):return True
        class Publisher:
            sequence=1;acknowledged=1;error=None
            def __init__(self,*args):pass
            def close(self):pass
        state=dict(state='running',source='synthetic_test',isolated_lcd=True,bulk_vectors=True,pid=123)
        prepare=SimpleNamespace(deploy=lambda *args:None,collect_diagnostics=lambda *args:{})
        spec=SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module:None))
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary);profile=path/'profile.json';profile.write_text(json.dumps(dict(address='192.168.2.99:9009')))
            modules={'jupyter_client':SimpleNamespace(JupyterBoard=Board),
                'control_jupyter':SimpleNamespace(request=lambda *args,**kwargs:state.copy(),PROFILE=profile),
                'host.action_status_publisher':SimpleNamespace(Publisher=Publisher)}
            client=SimpleNamespace(session=123,close=lambda:None)
            with patch.dict(sys.modules,modules),patch.object(subject,'operation_lock',lambda project:nullcontext()), \
                 patch.object(subject.importlib.util,'spec_from_file_location',return_value=spec), \
                 patch.object(subject.importlib.util,'module_from_spec',return_value=prepare), \
                 patch.object(subject,'connect',return_value=client), \
                 patch.object(subject,'latency_series',return_value={'deadline_misses':0}), \
                 patch.object(subject,'veto_check',side_effect=TimeoutError('veto reply late')), \
                 patch.object(subject,'restore_service',return_value={'restored_service_verified':True}), \
                 patch.object(sys,'argv',['benchmark','--prepare-board','--samples','1','--out',str(path/'report.json')]), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(subject.main(),1)
            result=json.loads((path/'report.json').read_text())
            self.assertFalse(result['passed']);self.assertIn('veto reply late',result['error'])
            self.assertTrue(result['restored_service_verified'])

    def test_interrupted_board_probe_restores_previous_service_mode_and_records_failure(self):
        calls=[]
        class Board:
            def request(self,*args,**kwargs):pass
            def authenticated(self):return True
        state=dict(state='running',source='host_observation',isolated_lcd=False,bulk_vectors=False,pid=123)
        def request(operation,**kwargs):
            calls.append((operation,kwargs))
            if operation=='ensure':state.update(state='running',**{k:v for k,v in kwargs.items() if k!='board_client'})
            return dict(state)
        prepare=SimpleNamespace(deploy=lambda board,profile,call:call('stop'))
        spec=SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module:None))
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary);profile=path/'profile.json';profile.write_text(json.dumps(dict(address='192.168.2.99:9009')))
            modules={'jupyter_client':SimpleNamespace(JupyterBoard=Board),
                'control_jupyter':SimpleNamespace(request=request,PROFILE=profile),
                'fpga_service_probe':SimpleNamespace(probe=lambda address:calls.append(('probe',address)))}
            with patch.dict(sys.modules,modules),patch.object(subject,'operation_lock',lambda project:nullcontext()), \
                 patch.object(subject.importlib.util,'spec_from_file_location',return_value=spec), \
                 patch.object(subject.importlib.util,'module_from_spec',return_value=prepare), \
                 patch.object(subject,'connect',side_effect=KeyboardInterrupt), \
                 patch.object(sys,'argv',['benchmark','--prepare-board','--samples','1','--out',str(path/'report.json')]),redirect_stdout(io.StringIO()):
                self.assertEqual(subject.main(),1)
            result=json.loads((path/'report.json').read_text());self.assertTrue(result['interrupted']);self.assertFalse(result['passed'])
            self.assertTrue(result['restored_service_verified']);self.assertEqual(state['source'],'host_observation')
            self.assertFalse(state['isolated_lcd']);self.assertFalse(state['bulk_vectors'])

    def test_lcd_evidence_requires_its_own_process_matching_reply_and_image_hash(self):
        png=b'fake-lcd-bytes';expected=dict(session=123,sequence=2,mask=1)
        health=dict(pid=99,display_ready=True,renderer_pid=100)
        state=dict(expected,renderer_pid=100,render_worker='process',source='synthetic_test',image_sha256=hashlib.sha256(png).hexdigest())
        class Board:
            def request(self,method,path):
                if path.endswith('.png'):return SimpleNamespace(content=png)
                return SimpleNamespace(json=lambda:health if path.endswith('service_health.json') else state)
        active={'board_root':'/home/xilinx/jupyter_notebooks/fpga_fpg1_v3_b35_20261113'}
        with tempfile.TemporaryDirectory() as temporary:
            out=Path(temporary)/'lcd.png'
            result=subject.display_check(Board(),active,99,expected,out,timeout=0)
            self.assertTrue(result['passed']);self.assertEqual(out.read_bytes(),png)
            state['sequence']=1
            with self.assertRaises(TimeoutError):subject.display_check(Board(),active,99,expected,out,timeout=0)
            state['sequence']=2;state['image_sha256']='wrong'
            with self.assertRaises(TimeoutError):subject.display_check(Board(),active,99,expected,out,timeout=0)

    def test_stopped_service_is_restored_without_requiring_a_live_probe(self):
        calls=[]
        def request(operation,**kwargs):calls.append((operation,kwargs));return {'state':'stopped'}
        with patch.dict(sys.modules,{'fpga_service_probe':SimpleNamespace(probe=lambda _:self.fail('stopped service must not be probed'))}):
            result=subject.restore_service(request,{'state':'stopped'},'192.168.2.99:9009')
        self.assertTrue(result['restored_service_verified']);self.assertEqual(calls,[('stop',{})])

    def test_original_running_service_modes_are_restored_and_probed(self):
        calls=[]
        def request(operation,**kwargs):calls.append((operation,kwargs));return {'state':'running'}
        with patch.dict(sys.modules,{'fpga_service_probe':SimpleNamespace(probe=lambda address:calls.append(('probe',address)))}):
            subject.restore_service(request,dict(state='running',source='host_observation',isolated_lcd=False,bulk_vectors=False),'192.168.2.99:9009')
        self.assertEqual(calls,[('ensure',dict(source='host_observation',lcd=True,isolated_lcd=False,bulk_vectors=False)),('probe','192.168.2.99:9009')])

    def test_veto_and_latching_are_measured_by_real_dispatch_callback_counts(self):
        client,profile=client_and_profile()
        result=subject.veto_check(client,profile,20000)
        self.assertTrue(result['passed']);self.assertEqual(result['accepted_callbacks'],1)
        self.assertEqual(result['rejected_callbacks'],0);self.assertEqual([r['decision'] for r in result['records']],['ALLOW','DENY','DENY'])
        client.close()
    def test_latency_report_uses_observed_time_not_nominal_pl_cycles(self):
        client,profile=client_and_profile();now=[0]
        original=client.send_sample
        def delayed(sample):now[0]+=5_000_000;return original(sample)
        client.send_sample=delayed
        with redirect_stdout(io.StringIO()):
            report=subject.latency_series(client,profile,4,20000,clock_ns=lambda:now[0],sleeper=lambda seconds:None)
        self.assertEqual(report['rtt_ms']['99'],5.0);self.assertEqual(report['matched'],4);self.assertEqual(report['deadline_misses'],0)
        client.close()
    def test_matching_but_late_replies_are_deadline_misses(self):
        client,profile=client_and_profile();now=[0];original=client.send_sample
        def delayed(sample):now[0]+=25_000_000;return original(sample)
        client.send_sample=delayed
        with redirect_stdout(io.StringIO()):
            report=subject.latency_series(client,profile,3,20000,clock_ns=lambda:now[0],sleeper=lambda seconds:None)
        self.assertEqual(report['matched'],3);self.assertEqual(report['deadline_misses'],3)
        client.close()


if __name__=='__main__':unittest.main()
