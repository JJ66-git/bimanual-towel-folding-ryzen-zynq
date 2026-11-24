"""Snapshot reads actual HTTP-shaped file bytes; no execute/upload/control calls."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from urllib.parse import unquote

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('board_snapshot_subject',ROOT/'tools/snapshot_board.py')
subject=importlib.util.module_from_spec(spec);spec.loader.exec_module(subject)
REMOTE='/home/xilinx/jupyter_notebooks/fpga_fpg1_v3_b35_test'


class Board:
    def __init__(self):
        self.files={name:b'# frozen runtime\n' for name in ('board/fpg1_v3_server.py','board/fold_bridge.py','board/service_control.py','host/fold_protocol.py','screen/cooperative_view.py','config/guard_profile.json')}
        self.files['overlay/fpg1_b35_test.bit']=b'\x00\xff\x10\x80'
        self.files['runs/service_state.json']=b'{"source":"host_observation"}'
        self.methods=[];self.changed=False
    def request(self,method,url,params=None):
        self.methods.append(method);path=unquote(url).split('fpga_fpg1_v3_b35_test/',1)[1]
        if url.startswith('/files/'):
            data=self.files[path]
            if self.changed and path=='board/fold_bridge.py':data+=b'changed'
            return SimpleNamespace(content=data)
        if path in self.files:
            model=dict(type='file',size=len(self.files[path]),last_modified='original',name=Path(path).name)
        else:
            children=set(name[len(path)+1:].split('/')[0] for name in self.files if name.startswith(path+'/'))
            if not children:
                error=RuntimeError('not found');error.response=SimpleNamespace(status_code=404);raise error
            model=dict(type='directory',content=[])
            for name in sorted(children):
                child=path+'/'+name
                model['content'].append(dict(name=name,type='file' if child in self.files else 'directory',size=len(self.files.get(child,b'')),last_modified='original'))
        return SimpleNamespace(json=lambda:model)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_preserves_binary_and_source_bytes_without_mutating_board(self):
        board=Board()
        with tempfile.TemporaryDirectory() as temporary:
            report=subject.snapshot(board,dict(board_root=REMOTE),Path(temporary))
            self.assertTrue(report['passed']);self.assertEqual(set(board.methods),{'GET'})
            for name,data in board.files.items():self.assertEqual((Path(temporary)/'files'/name).read_bytes(),data)
    def test_changed_board_file_cannot_produce_a_passed_manifest(self):
        board=Board();board.changed=True
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RuntimeError,'changed during snapshot'):subject.snapshot(board,dict(board_root=REMOTE),Path(temporary))
            self.assertFalse((Path(temporary)/'board_snapshot.json').exists());self.assertEqual(set(board.methods),{'GET'})
    def test_missing_bitstream_is_not_a_complete_board_backup(self):
        board=Board();del board.files['overlay/fpg1_b35_test.bit']
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(Exception):subject.snapshot(board,dict(board_root=REMOTE),Path(temporary))
            self.assertFalse((Path(temporary)/'board_snapshot.json').exists())


if __name__=='__main__':unittest.main()
