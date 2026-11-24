"""New field runs stay separate from the fixed historical calibration run."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('field_visual_replay_test',ROOT/'npu_deploy/fpga_authority/visual_replay.py')
subject=importlib.util.module_from_spec(spec);spec.loader.exec_module(subject)


class FieldSplitTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup);self.root=Path(self.directory.name)
        comparison=self.root/'local_environment_record/task_failure_review_20261114/success_failure_comparison.json'
        comparison.parent.mkdir(parents=True);comparison.write_text('{"runs":[]}')
        self.recording=self.root/'recording';self.recording.mkdir()
        for side in ('left','middle','right'):(self.recording/(side+'.avi')).write_bytes(b'unit-fixture-not-decoded')
        self.row=dict(label='field_run',recording=str(self.recording),outcome='success',
            outcome_source='unit-test operator fixture',source_kind='CURRENT_ROBOT_CAMERA_RECORDING')
    def run_manifest(self,rows):
        p=self.root/'field.json';p.write_text(json.dumps(dict(schema='field_visual_runs_v1',runs=rows)))
        return subject.dataset(self.root,p)[0]
    def test_success_and_failure_are_always_held_out(self):
        self.assertEqual(self.run_manifest([self.row])[0]['split'],'held_out_success')
        self.assertEqual(self.run_manifest([{**self.row,'outcome':'failure'}])[0]['split'],'held_out_failure')
    def test_duplicates_and_unsafe_or_unlabelled_sources_are_rejected(self):
        for rows in ([self.row,self.row],[{**self.row,'label':'../escape'}],[{**self.row,'outcome_source':''}],
                     [{**self.row,'source_kind':'phone'}]):
            with self.assertRaises(ValueError):self.run_manifest(rows)


if __name__=='__main__':unittest.main()
