"""Fault, replay, temporal evidence and overlap checks without device APIs."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'npu_deploy/fpga_authority'),str(ROOT/'fpga_integration/fpga_code/fpga_workspace')]
import faults
import replay
from host.reference import load_profile
from host.protocol import UNKNOWN,CHECK_CORRECTION
from host_acceptance import load_overlap
import host_acceptance
from visual_evidence import VisualEvidenceGate


class OfflineAuthorityTests(unittest.TestCase):
    def test_missing_hardware_stops_before_any_worker_or_board_mutation(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(host_acceptance,'capabilities',return_value=dict(board_tcp=False,board_error='EPERM',kfd=False,dri=False,xdna=False)), \
             patch.object(host_acceptance.subprocess,'Popen') as spawn,patch.object(sys,'argv',['acceptance','--execute','--out',temporary]),redirect_stdout(io.StringIO()):
            self.assertEqual(host_acceptance.main(),2);spawn.assert_not_called()
            report=json.loads((Path(temporary)/'acceptance.json').read_text());self.assertEqual(report['status'],'blocked')
            self.assertFalse(report['eligible_for_policy_execution'])

    def test_all_real_client_fault_paths_prevent_rejected_dispatch_and_latch(self):
        with redirect_stdout(io.StringIO()):report=faults.run_matrix()
        self.assertTrue(report['passed']);self.assertEqual(len(report['cases']),19)
        self.assertFalse(report['real_pl']);self.assertFalse(report['eligible_for_policy_execution'])
        ambiguous=next(r for r in report['cases'] if r['case']=='dispatch_error')
        self.assertEqual(ambiguous['dispatch_attempts'],1);self.assertTrue(ambiguous['latched'])
        self.assertIn('unknown',ambiguous['dispatch_outcome'])

    def test_historic_missing_age_stays_unknown_and_previous_correction_is_reconstructed(self):
        profile=load_profile();pose=[v/1e6 for v in profile['start']];correction=[0.]*14;correction[0]=.01
        rows=[]
        for step in range(2):rows.append(dict(step=step,tick_monotonic_s=1+step*.033,target=pose,measured=pose,previous=pose,act_action=pose,correction=correction))
        pairs=list(replay.samples_from_trace({'rows':rows},profile))
        self.assertEqual(pairs[0][1].feedback_age_us,UNKNOWN);self.assertEqual(pairs[0][1].queue_depth,UNKNOWN)
        self.assertEqual(pairs[0][1].previous_correction,(0,)*14);self.assertEqual(pairs[1][1].previous_correction[0],10000)
        self.assertTrue(pairs[0][1].flags&CHECK_CORRECTION)

    def test_overlap_requires_actual_timing_intervals_not_provider_labels(self):
        model={'rows':[{'act':{'started_ns':10,'returned_ns':20},'smol_guided':{'started_ns':30,'returned_ns':40}}]}
        board={'latency':{'rows':[{'started_ns':15,'returned_ns':17},{'started_ns':22,'returned_ns':25}]}}
        r=load_overlap(model,board);self.assertEqual(r['samples_overlapping_model_inference'],1);self.assertEqual(r['samples'],2)


class VisualTemporalTests(unittest.TestCase):
    def setUp(self):self.gate=VisualEvidenceGate();self.gate.begin(123,7)
    def frame(self,index,**changes):
        at=1_000_000_000+index*100_000_000
        values=dict(session=123,event=7,frame_id=index,captured_ns=at,now_ns=at,retreat_ready=True,
            visible=True,geometry_matches=True,center=(200.,200.),area=10000.)
        values.update(changes);return self.gate.observe(**values)
    def test_stable_evidence_is_only_a_candidate_never_physical_success(self):
        for i in range(5):result=self.frame(i)
        self.assertEqual(result['state'],'MATCH_CANDIDATE');self.assertFalse(result['task_success_certified'])
    def test_unknown_retreat_and_empty_cloth_never_count_as_a_match(self):
        self.assertEqual(self.frame(0,retreat_ready=None)['reason'],'arm_retreat_unverified')
        self.assertEqual(self.frame(1,visible=False)['reason'],'cloth_missing_or_occluded')
        self.assertEqual(len(self.gate.window),0)
    def test_old_event_duplicate_and_stale_frames_clear_evidence(self):
        self.frame(0);self.assertEqual(self.frame(1,event=8)['state'],'UNKNOWN');self.assertEqual(len(self.gate.window),0)
        self.frame(2);self.assertEqual(self.frame(2)['reason'],'duplicate_or_reordered_frame')
        self.assertEqual(self.frame(3,now_ns=2_000_000_000)['reason'],'stale_or_future_frame')
    def test_gap_drift_and_changed_session_cannot_accumulate_stability(self):
        self.frame(0);self.frame(1);self.frame(2,center=(220.,200.));self.assertEqual(len(self.gate.window),1)
        self.frame(8);self.assertEqual(len(self.gate.window),1)
        self.gate.begin(124,1);self.assertEqual(self.frame(9)['state'],'UNKNOWN')
    def test_nonfinite_geometry_is_unknown(self):self.assertEqual(self.frame(0,area=float('nan'))['reason'],'invalid_geometry')


if __name__=='__main__':unittest.main()
