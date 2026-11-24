"""Checkouts share rig ownership and reuse login without rewriting its source."""
import fcntl
import http.cookiejar
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


class OwnershipTests(unittest.TestCase):
    def layout(self,directory):
        root=Path(directory);origin=root/'original';checkout=root/'checkout'
        (checkout/'baseline').mkdir(parents=True)
        (checkout/'baseline/source_files.json').write_text(json.dumps({'source_workspace':str(origin)}))
        return origin/ROOT.name,checkout/ROOT.name

    def test_all_checkouts_contend_for_the_original_rig_lock(self):
        subject=module('rig_lock_subject',ROOT/'npu_deploy/operation_lock_path.py')
        with tempfile.TemporaryDirectory() as directory:
            original,checkout=self.layout(directory)
            expected=subject.operation_lock_path(original)
            self.assertEqual(subject.operation_lock_path(checkout),expected)
            expected.parent.mkdir(parents=True)
            with expected.open('a+b') as owner:
                fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaisesRegex(RuntimeError,'不能并发'):
                    with subject.operation_lock(checkout):self.fail('second owner entered')
            with subject.operation_lock(checkout) as actual:self.assertEqual(actual,expected)

    def test_saved_login_is_reused_in_memory_without_copying_credentials_into_git(self):
        subject=module('rig_jupyter_subject',ROOT/'npu_deploy/fpga_board/jupyter_client.py')
        with tempfile.TemporaryDirectory() as directory:
            original,checkout=self.layout(directory)
            source=original/'npu_deploy/artifacts/fpga_board/private/jupyter_cookies.txt'
            source.parent.mkdir(parents=True)
            jar=http.cookiejar.MozillaCookieJar(str(source))
            jar.set_cookie(http.cookiejar.Cookie(0,'test_session','unit-test-only',None,False,
                '192.168.2.99',False,False,'/',True,False,None,True,None,None,{},False))
            jar.save(ignore_discard=True,ignore_expires=True);before=source.read_bytes()
            private=checkout/'npu_deploy/artifacts/fpga_board/private'
            with patch.object(subject,'ROOT',checkout),patch.object(subject,'PRIVATE',private):
                client=subject.JupyterBoard()
            self.assertEqual([(c.name,c.value) for c in client.cookies],[('test_session','unit-test-only')])
            self.assertEqual(Path(client.cookies.filename),private/'jupyter_cookies.txt')
            self.assertFalse(Path(client.cookies.filename).exists());self.assertEqual(source.read_bytes(),before)
            client.session.close()


if __name__=='__main__':unittest.main()
