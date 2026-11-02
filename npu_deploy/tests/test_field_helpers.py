import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import field_budget
import check_preparation_result
import fpga_service_probe


class HelperTests(unittest.TestCase):
    def test_budget_updates_only_live_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);site=p/'site.json';profile=p/'profile.json'
            site.write_text(json.dumps({'max_actions':900,'handoff_step':300,'task':'keep'}))
            profile.write_text(json.dumps({'max_actions':750,'handoff_step':300}))
            with patch.object(field_budget,'SITE',site), patch.object(field_budget,'PROFILES',(profile,)):
                with self.assertRaises(RuntimeError):field_budget.check()
                field_budget.check(apply=True)
                self.assertEqual(json.loads(site.read_text()),{'max_actions':750,'handoff_step':300,'task':'keep'})
                field_budget.check()

    def test_preparation_requires_two_complete_feedback_sides(self):
        valid={'rx_frames':5,'gripper_frames':1,'error_frames':0,
               'joint_enable':{str(i):False for i in range(1,7)}}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'prepare.log'
            path.write_text(''.join('RECEIVE_ONLY '+s+'='+json.dumps(valid)+'\n' for s in ('left','right')))
            check_preparation_result.check(path)
            path.write_text('RECEIVE_ONLY left='+json.dumps(valid)+'\n')
            with self.assertRaises(RuntimeError):check_preparation_result.check(path)

    def test_probe_uses_status_only_and_closes(self):
        calls=[]
        class FakeClient:
            def __init__(self,address,timeout):calls.append(('open',address,timeout))
            def send(self,*args):calls.append(('send',args));return {'sequence':0}
            def close(self):calls.append(('close',))
        with patch.object(fpga_service_probe,'Client',FakeClient), contextlib.redirect_stdout(io.StringIO()):
            fpga_service_probe.probe('192.0.2.1:9009')
        self.assertEqual(calls,[('open',('192.0.2.1',9009),2.0),('send',('WAITING','IDLE','HOST')),('close',)])


if __name__=='__main__': unittest.main()
