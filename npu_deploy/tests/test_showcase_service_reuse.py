"""Managed service reuse and mode selection, with no board processes or sockets."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch

ROOT=Path(__file__).resolve().parents[2]


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


class ServiceReuseTests(unittest.TestCase):
    def test_screen_stays_disabled_on_reuse_and_is_reenabled_explicitly(self):
        with patch.dict(sys.modules,{'jupyter_client':SimpleNamespace(JupyterBoard=object)}):
            control=module('lcd_reuse_subject',ROOT/'npu_deploy/fpga_board/control_jupyter.py')
        for requested,expected in ((False,['status','start']),(True,['status','stop','start'])):
            calls=[]
            def request(operation,*args,**kwargs):
                calls.append(operation)
                if operation=='status':return dict(state='running',source='host_observation',lcd=False)
                if operation=='start':self.assertEqual(args[1],requested)
                return {'state':'running'}
            control.ensure_service(requester=request,lcd=requested)
            self.assertEqual(calls,expected)
    def test_authority_modes_are_reused_and_changed_only_when_needed(self):
        with patch.dict(sys.modules,{'jupyter_client':SimpleNamespace(JupyterBoard=object)}):
            control=module('authority_mode_subject',ROOT/'npu_deploy/fpga_board/control_jupyter.py')
        for enabled,expected in [(True,['status','start']),(False,['status','stop','start'])]:
            calls=[]
            def request(operation,*args,**kwargs):
                calls.append(operation)
                if operation=='status':return dict(state='running',source='host_observation',isolated_lcd=enabled,bulk_vectors=enabled)
                if operation=='start':self.assertEqual(kwargs,dict(isolated_lcd=True,bulk_vectors=True))
                return {'state':'running'}
            control.ensure_service(requester=request,isolated_lcd=True,bulk_vectors=True)
            self.assertEqual(calls,expected)

    def test_mode_is_reused_without_stop_but_switched_explicitly_when_needed(self):
        with patch.dict(sys.modules,{'jupyter_client':SimpleNamespace(JupyterBoard=object)}):
            control=module('managed_control_subject',ROOT/'npu_deploy/fpga_board/control_jupyter.py')
        for state,expected in [({'state':'running','source':'host_observation'},['status','start']),
                               ({'state':'stopped'},['status','start']),
                               ({'state':'running','source':'recorded_replay'},['status','stop','start'])]:
            calls=[]
            def request(operation,*args):
                calls.append(operation);return state if operation=='status' else {'state':'running'}
            control.ensure_service(requester=request)
            self.assertEqual(calls,expected)

    def test_existing_service_start_cancels_a_scheduled_stop_without_reprogramming(self):
        board=module('board_control_subject',ROOT/'fpga_integration/fpga_code/fpga_workspace/board/service_control.py')
        with tempfile.TemporaryDirectory() as directory:
            board.ROOT=Path(directory);board.STATE=board.ROOT/'service_state.json'
            board.STATE.write_text(json.dumps(dict(pid=123,source='host_observation',stop_token='old-finish-token')))
            with patch.object(board,'_owned',return_value=True),patch.object(board.subprocess,'Popen') as spawn:
                result=board.control('start')
            self.assertEqual(result['state'],'running');spawn.assert_not_called()
            self.assertIsNone(json.loads(board.STATE.read_text())['stop_token'])

    def test_only_recorded_replay_selects_the_booth_display(self):
        for source,booth_expected in [('host_observation',False),('recorded_replay',True)]:
            with self.subTest(source=source),tempfile.TemporaryDirectory() as directory:
                board=module('board_start_subject',ROOT/'fpga_integration/fpga_code/fpga_workspace/board/service_control.py')
                board.ROOT=Path(directory);board.STATE=board.ROOT/'runs/service_state.json'
                with patch.object(board,'_owned',return_value=False), \
                     patch.object(board.socket,'socket',return_value=MagicMock()), \
                     patch.object(board.subprocess,'Popen',return_value=SimpleNamespace(pid=os.getpid())) as spawn, \
                     patch.dict(sys.modules,{'board.fpg1_v3_server':SimpleNamespace(DEFAULT_BITFILE=Path('/fake/selected_b35.bit'))}):
                    board.control('start',source=source)
                command=spawn.call_args.args[0]
                self.assertEqual('--booth' in command,booth_expected)
                self.assertIn('--lcd',command)
                self.assertIn('/fake/selected_b35.bit',command)


if __name__=='__main__':unittest.main()
