"""A previous preflight is evidence only for its unchanged inputs/runtime and boot."""
import datetime
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from npu_preflight_reuse import reusable


class PreflightReuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.now=1800000000.;self.fingerprint={'kernel':'test','files':{}}
        self.art=self.root/'npu_deploy/artifacts/smol_vision';self.art.mkdir(parents=True)
        self.session=self.root/'session';self.session.mkdir()
        self.file=self.root/'npu_deploy/artifacts/npu_preflight/latest_success.json';self.file.parent.mkdir()
        for p,data in [(self.art/'smol_vision.onnx',b'model'),(self.art/'fixture.npz',b'fixture'),(self.session/'npu.log',b'NPU verified')]:p.write_bytes(data)
        digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        self.report=dict(passed=True,npu_partition_confirmed=True,valid_image_outputs=3,
            checked_at=datetime.datetime.fromtimestamp(self.now-10,datetime.timezone.utc).isoformat(),
            session=str(self.session),onnx_sha256=digest(self.art/'smol_vision.onnx'),
            fixture_sha256=digest(self.art/'fixture.npz'),log_sha256=digest(self.session/'npu.log'))
        self.save()
    def tearDown(self):self.tmp.cleanup()
    def save(self):self.file.write_text(json.dumps(self.report))
    def valid(self):return reusable(self.root,now=self.now,uptime=1000,fingerprint=self.fingerprint)[0]
    def test_current_boot_legacy_evidence_can_be_reused(self):self.assertTrue(self.valid())
    def test_previous_boot_must_run_again(self):
        self.report['checked_at']=datetime.datetime.fromtimestamp(self.now-2000,datetime.timezone.utc).isoformat();self.save()
        self.assertFalse(self.valid())
    def test_model_fixture_or_log_changes_invalidate_evidence(self):
        for path in (self.art/'smol_vision.onnx',self.art/'fixture.npz',self.session/'npu.log'):
            with self.subTest(path=path):
                before=path.read_bytes();path.write_bytes(before+b'changed')
                self.assertFalse(self.valid());path.write_bytes(before)
    def test_cpu_fallback_does_not_qualify(self):
        self.report['npu_partition_confirmed']=False;self.save();self.assertFalse(self.valid())
    def test_runtime_fingerprint_change_invalidates(self):
        self.report['runtime_fingerprint']={'kernel':'previous','files':{}};self.save()
        self.assertFalse(self.valid())
    def test_matching_new_runtime_receipt_is_reused(self):
        self.report['runtime_fingerprint']=self.fingerprint;self.save();self.assertTrue(self.valid())
    def test_updated_runtime_invalidates_a_legacy_receipt(self):
        self.fingerprint['files']['library']={'mtime_ns':int(self.now*1e9)}
        self.assertFalse(self.valid())


if __name__=='__main__':unittest.main()
