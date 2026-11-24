"""Restore real temporary files, exercise tamper/startup/transfer failure paths."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
runtime=module('runtime_restore_subject',ROOT/'tools/restore_runtime.py')
board=module('board_restore_subject',ROOT/'tools/restore_board.py')


class RuntimeRestoreTests(unittest.TestCase):
    def test_mid_commit_failure_rolls_back_previously_committed_roles(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);rows=[]
            for name in ('first','second'):
                backup=root/'snapshot'/name;backup.mkdir(parents=True);(backup/'item').write_text('new '+name)
                destination=root/'destination'/name;destination.mkdir(parents=True);(destination/'item').write_text('old '+name)
                rows.append(dict(role=name,backup=str(backup),destination=str(destination),files=runtime.freeze.inventory(backup)))
            original=Path.rename;failed=[False]
            def rename(path,target):
                if path.name=='payload' and Path(target).name=='second' and not failed[0]:failed[0]=True;raise OSError('injected second commit failure')
                return original(path,target)
            with patch.object(Path,'rename',rename):
                with self.assertRaises(OSError):runtime.restore(rows)
            for name in ('first','second'):self.assertEqual((root/'destination'/name/'item').read_text(),'old '+name)

    def fixture(self,root):
        snapshot=root/'snapshot';snapshot.mkdir();backup=snapshot/'python';backup.mkdir();(backup/'module.py').write_text('new content')
        (backup/'python').symlink_to('/usr/bin/python3')
        (snapshot/'freeze_report.json').write_text(json.dumps(dict(schema='flexifold_runtime_freeze_v1',passed=True,roles=['python'])))
        (snapshot/'python.manifest.json').write_text(json.dumps(dict(role='python',source=str(root/'origin/env'),files=runtime.freeze.inventory(backup))))
        return snapshot
    def test_plan_retains_original_and_apply_preserves_symlinks_without_hardlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);snapshot=self.fixture(root);target=root/'destination/env';target.mkdir(parents=True);(target/'module.py').write_text('old content')
            rows=runtime.plan(snapshot,root/'origin',root/'destination')
            self.assertEqual((target/'module.py').read_text(),'old content')
            result=runtime.restore(rows);self.assertTrue(result['passed']);self.assertEqual((target/'module.py').read_text(),'new content')
            self.assertTrue((target/'python').is_symlink());self.assertNotEqual((target/'module.py').stat().st_ino,(snapshot/'python/module.py').stat().st_ino)
            retained=Path(result['restored'][0]['retained_previous']);self.assertEqual((retained/'module.py').read_text(),'old content')
    def test_tamper_is_rejected_before_any_destination_is_created(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);snapshot=self.fixture(root);(snapshot/'python/module.py').write_text('corrupt')
            with self.assertRaisesRegex(ValueError,'integrity'):runtime.plan(snapshot,root/'origin',root/'destination')
            self.assertFalse((root/'destination').exists())
    def test_bad_staged_copy_leaves_original_intact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);snapshot=self.fixture(root);target=root/'destination/env';target.mkdir(parents=True);(target/'module.py').write_text('old content')
            rows=runtime.plan(snapshot,root/'origin',root/'destination');original=runtime.shutil.copytree
            def damaged(source,destination,**kwargs):
                result=original(source,destination,**kwargs);(destination/'module.py').write_text('bad staged bytes');return result
            with patch.object(runtime.shutil,'copytree',side_effect=damaged):
                with self.assertRaisesRegex(RuntimeError,'Staged copy'):runtime.restore(rows)
            self.assertEqual((target/'module.py').read_text(),'old content')


class BoardRestoreTests(unittest.TestCase):
    def fixture(self,root):
        snapshot=root/'snapshot';path=snapshot/'files/board/server.py';path.parent.mkdir(parents=True);path.write_bytes(b'old verified code')
        dynamic=snapshot/'files/runs/service_state.json';dynamic.parent.mkdir();dynamic.write_text(json.dumps(dict(pid=999,source='host_observation')))
        profile={'board_root':'/home/xilinx/jupyter_notebooks/fpga_fpg1_v3_b35_test'}
        rows=[dict(path='board/server.py',bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),dynamic=False),
            dict(path='runs/service_state.json',bytes=dynamic.stat().st_size,sha256=hashlib.sha256(dynamic.read_bytes()).hexdigest(),dynamic=True)]
        (snapshot/'board_snapshot.json').write_text(json.dumps(dict(schema='flexifold_board_snapshot_v1',passed=True,board_root=profile['board_root'],files=rows)))
        return snapshot,profile
    def run_restore(self,snapshot,profile,*,fail_upload=False,fail_start=False,fail_capture=False,current=b'new current code'):
        data={'board/server.py':current};calls=[];ready=[];outer=self
        class Board:
            broken=fail_upload
            def request(self,method,path,**kwargs):return SimpleNamespace(content=data[path.split('/files/',1)[1].split('/',1)[1]])
            def upload(self,path,payload):
                calls.append(('upload',path))
                if self.broken:self.broken=False;raise OSError('injected transfer failure')
                data[path.split('/',1)[1]]=payload
        starts=[0]
        def control(operation,**kwargs):
            calls.append((operation,kwargs))
            if operation=='status':return dict(state='running',source='recorded_replay',isolated_lcd=True,bulk_vectors=True)
            if operation=='ensure':
                starts[0]+=1
                if fail_start and starts[0]==1:raise RuntimeError('injected startup failure')
            return {'state':'running'}
        def capture(*args):
            calls.append(('capture',{}))
            if fail_capture:raise RuntimeError('injected snapshot failure')
        try:result=board.apply_restore(Board(),profile,snapshot,control,capture,recovery_directory=snapshot.parent/'recovery',verify_ready=lambda:ready.append(True))
        except Exception as error:return data,calls,error
        return data,calls,result
    def test_verified_static_files_restore_but_old_pid_is_never_uploaded(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot,profile=self.fixture(Path(temporary));data,calls,result=self.run_restore(snapshot,profile)
            self.assertTrue(result['passed']);self.assertEqual(data['board/server.py'],b'old verified code')
            self.assertFalse(any('service_state' in str(row) for row in calls))
            self.assertLess(next(i for i,r in enumerate(calls) if r[0]=='capture'),next(i for i,r in enumerate(calls) if r[0]=='stop'))
    def test_upload_and_startup_failures_restore_exact_prior_bytes_and_modes(self):
        for failure in ('fail_upload','fail_start'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as temporary:
                snapshot,profile=self.fixture(Path(temporary));data,calls,result=self.run_restore(snapshot,profile,**{failure:True})
                self.assertIsInstance(result,Exception);self.assertEqual(data['board/server.py'],b'new current code')
                last=next(row for row in reversed(calls) if row[0]=='ensure')
                self.assertEqual(last[1],dict(source='recorded_replay',lcd=True,isolated_lcd=True,bulk_vectors=True))
    def test_snapshot_failure_cannot_stop_or_upload_and_identical_bytes_reuse_service(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot,profile=self.fixture(Path(temporary));_,calls,error=self.run_restore(snapshot,profile,fail_capture=True)
            self.assertIsInstance(error,RuntimeError);self.assertFalse(any(row[0] in ('stop','upload') for row in calls))
            _,calls,result=self.run_restore(snapshot,profile,current=b'old verified code');self.assertTrue(result['passed']);self.assertEqual(calls,[])
    def test_corrupt_snapshot_never_connects(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot,profile=self.fixture(Path(temporary));(snapshot/'files/board/server.py').write_text('tampered')
            with self.assertRaisesRegex(ValueError,'integrity'):board.payloads(snapshot)


if __name__=='__main__':unittest.main()
