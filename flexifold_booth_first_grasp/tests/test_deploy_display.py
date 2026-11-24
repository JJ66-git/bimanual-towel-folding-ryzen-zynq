"""Exercise paired display sync/rollback against a temporary board filesystem."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

import prepare_hardware as subject

REMOTE='/home/xilinx/jupyter_notebooks/fpga_fpg1_v3_b35_test'


class FilesystemBoard:
    def __init__(self,root,fail_upload=None):
        self.root=root;self.uploads=0;self.fail_upload=fail_upload
    def execute(self,code,timeout=40):
        output=io.StringIO()
        try:
            with redirect_stdout(output):exec(code.replace(repr(REMOTE),repr(str(self.root))),{})
            return dict(passed=True,output=output.getvalue())
        except Exception as error:return dict(passed=False,errors=str(error),output=output.getvalue())
    def upload(self,path,data):
        self.uploads+=1
        if self.uploads==self.fail_upload:raise OSError('fixture transfer failure')
        target=self.root/path.split('/',1)[1];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)


class DisplayDeployTests(unittest.TestCase):
    def setUp(self):
        self.capture=patch.object(subject,'capture_board_rollback',return_value=Path('/fixture/board_snapshot.json')).start()
        self.addCleanup(patch.stopall)
    def populate(self,root,current):
        originals={}
        for name in subject.FILES:
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
            originals[name]=(subject.RUNTIME/name).read_bytes() if current else b'# prior board file\n'
            path.write_bytes(originals[name])
        return originals
    def test_unchanged_files_reuse_service_without_upload_or_stop(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);self.populate(root,True);board=FilesystemBoard(root);control=Mock()
            with redirect_stdout(io.StringIO()):subject.deploy(board,dict(board_root=REMOTE),control)
            control.assert_not_called();self.assertEqual(board.uploads,0)
            self.capture.assert_not_called()
    def test_changed_pair_is_backed_up_and_fonts_are_hash_checked_as_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=self.populate(root,False);board=FilesystemBoard(root);control=Mock()
            with redirect_stdout(io.StringIO()):subject.deploy(board,dict(board_root=REMOTE),control)
            control.assert_called_once_with('stop');self.assertEqual(board.uploads,len(subject.FILES))
            self.capture.assert_called_once()
            backup=next((root/'.booth_backups').iterdir())
            for name in subject.FILES:
                self.assertEqual((root/name).read_bytes(),(subject.RUNTIME/name).read_bytes())
                self.assertEqual((backup/name).read_bytes(),old[name])
    def test_transfer_failure_restores_all_prior_files_and_restarts_task_display(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=self.populate(root,False);board=FilesystemBoard(root,fail_upload=3);control=Mock()
            with redirect_stdout(io.StringIO()),self.assertRaisesRegex(OSError,'fixture transfer failure'):
                subject.deploy(board,dict(board_root=REMOTE),control)
            self.assertEqual([call.args[0] for call in control.call_args_list],['stop','start'])
            for name,data in old.items():self.assertEqual((root/name).read_bytes(),data)
    def test_no_service_is_stopped_when_the_rollback_snapshot_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);self.populate(root,False);board=FilesystemBoard(root);control=Mock()
            self.capture.side_effect=RuntimeError('snapshot could not be verified')
            with redirect_stdout(io.StringIO()),self.assertRaisesRegex(RuntimeError,'snapshot could not'):
                subject.deploy(board,dict(board_root=REMOTE),control)
            control.assert_not_called();self.assertEqual(board.uploads,0)


if __name__=='__main__':unittest.main()
