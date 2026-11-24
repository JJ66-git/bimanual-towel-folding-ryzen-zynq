"""Display telemetry semantics: counts, provenance, freshness and boundedness."""
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'amd_hybrid_deploy_20261031'))
from shared_runtime import dashboard_telemetry as telemetry
from shared_runtime.dashboard_view import DashboardState, chart_geometry, render_dashboard, freeze_view, diagnostic_payload


class TelemetryTests(unittest.TestCase):
    def setUp(self):self.token=telemetry.install()
    def tearDown(self):telemetry.uninstall(self.token)
    def run_telemetry(self,execute=True):
        return telemetry.RunTelemetry(execute=execute,max_actions=750,max_authority=.9)
    def tick(self,run,step,delta,source='SAFE',dispatched=True):
        run.tick(step=step,authority=.868,max_correction_rad=delta,source=source,dispatched=dispatched)

    def test_counts_use_successful_policy_dispatch_and_strict_threshold(self):
        run=self.run_telemetry()
        self.tick(run,0,.0001)  # Exactly the threshold does not count.
        self.tick(run,1,.107,'SmolVLA')
        self.tick(run,2,.02,'SAFE')  # A decaying correction still counts.
        self.tick(run,3,.4,'SmolVLA',dispatched=False)  # No successful send.
        value,_=telemetry.sample()
        self.assertEqual((value.corrected_steps,value.dispatched_steps,value.candidate_steps),(2,3,1))
        with self.assertRaises(FrozenInstanceError):value.authority=0.

    def test_gui_drops_do_not_drop_counters_and_new_run_resets(self):
        run=self.run_telemetry()
        for step in range(750):self.tick(run,step,.01)
        self.assertEqual(telemetry.sample()[0].corrected_steps,750)
        new=self.run_telemetry()
        self.assertEqual(telemetry.sample()[0].dispatched_steps,0)
        self.assertNotEqual(run.value.run_id,new.value.run_id)

    def test_read_only_never_counts_dispatches(self):
        run=self.run_telemetry(False)
        self.tick(run,0,.107,'SmolVLA')
        self.assertEqual(run.value.dispatched_steps,0)
        self.assertEqual(run.value.corrected_steps,0)
        self.assertEqual(run.value.authority,.868)

    def test_invalid_sample_keeps_last_valid_snapshot(self):
        run=self.run_telemetry();self.tick(run,0,.01)
        old=run.value
        run.tick(step=1,authority=float('nan'),max_correction_rad=.1,source='SAFE',dispatched=True)
        self.assertIs(run.value,old)

    def test_probe_read_only_and_retired_channel_never_light_up_board(self):
        owner=self.token[0]
        record=dict(kind='preflight_probe',dispatched=True,session=12,sequence=4,step=0,reference_match=True,violations=0)
        telemetry.publish_board(record,owner);self.assertIsNone(owner.board)
        record.update(kind='task_sample',dispatched=False)
        telemetry.publish_board(record,owner);self.assertIsNone(owner.board)
        record['dispatched']=True
        telemetry.publish_board(record,owner);self.assertEqual(owner.board.sequence,4)
        token2=telemetry.install()
        try:
            telemetry.publish_board(record,owner)
            self.assertIsNone(token2[0].board)
        finally:telemetry.uninstall(token2)

    def test_no_preview_and_broken_initialization_cannot_break_control(self):
        telemetry.uninstall(self.token)
        run=telemetry.create_run(execute=True,max_actions=750,max_authority=.9)
        run.tick(step=0,authority=1,max_correction_rad=.2,source='SAFE',dispatched=True)
        self.token=telemetry.install()
        with patch.object(telemetry,'RunTelemetry',side_effect=RuntimeError('display failure')):
            run=telemetry.create_run(execute=True,max_actions=750,max_authority=.9)
            run.state('running')
            run.tick(step=0,authority=1,max_correction_rad=.2,source='SAFE',dispatched=True)

    def test_history_deduplicates_gaps_and_resets_between_runs(self):
        state=DashboardState();run=self.run_telemetry();base=time.monotonic()
        for i in range(60):
            value=replace(run.value,sampled_mono_s=base+i*.2,authority=0.,run_state='running')
            state.ingest(dict(sampled=base,control=value))
            state.ingest(dict(sampled=base,control=value))
        self.assertEqual(len(state.history),50)
        last=state.control
        state.ingest(dict(sampled=base,control=replace(last,sampled_mono_s=base)))
        self.assertEqual(state.control,last)
        state.ingest(dict(sampled=base,control=replace(last,sampled_mono_s=base+15)))
        segments=chart_geometry(state,(0,0,100,100),base+15)
        self.assertEqual(len(segments),2)
        self.assertTrue(all(y==100 for points in segments for x,y in points))
        state.ingest(dict(sampled=base,control=replace(last,run_id='next',sampled_mono_s=base+16)))
        self.assertEqual(len(state.history),1)

    def test_board_counts_survive_gui_drops_and_history_is_bounded(self):
        owner=self.token[0]
        record=dict(kind='task_sample',dispatched=True,session=1,step=0,
                    reference_match=True,violations=0,host_round_trip_us=120.,board_processing_us=20)
        for i in range(200):
            record.update(sequence=i,step=i,reference_match=i%4!=0,violations=int(i%5==0))
            telemetry.publish_board(record,owner)
        self.assertEqual(owner.board.checked_count,200)
        self.assertEqual(owner.board.reference_matches,150)
        self.assertEqual(owner.board.reference_mismatches,50)
        self.assertEqual(owner.board.violation_count,40)
        self.assertEqual(len(owner.board_history),32)
        self.assertEqual(owner.board.round_trip_ms,.12)
        self.assertEqual(owner.board.board_processing_ms,.02)
        self.run_telemetry()
        self.assertIsNone(owner.board)
        self.assertEqual(owner.board_history,())

    def test_retired_run_and_wrong_session_cannot_replace_new_board_data(self):
        owner=self.token[0];old=self.run_telemetry();new=self.run_telemetry()
        record=dict(kind='task_sample',dispatched=True,session=1,sequence=0,step=0,reference_match=True,violations=0)
        telemetry.publish_board(record,owner,old.value.run_id)
        self.assertIsNone(owner.board)
        metadata=dict(address=['127.0.0.1',9009],profile_crc='01234567',controller_budget=750,fpga_budget=750)
        telemetry.publish_link(metadata,2,owner,new.value.run_id)
        telemetry.publish_board(record,owner,new.value.run_id)
        self.assertIsNone(owner.board)
        record['session']=2
        telemetry.publish_board(record,owner,new.value.run_id)
        self.assertEqual(owner.board.session,2)

    def test_freeze_is_display_only_and_diagnostic_has_no_camera_data(self):
        sys.path.insert(0,str(ROOT/'npu_deploy'))
        from demo_live_dashboard import demo_state
        live=demo_state('board-ok')
        frozen=freeze_view(live)
        old=frozen.board
        live.ingest(dict(sampled=time.monotonic(),control=replace(live.control,step=480,
            sampled_mono_s=live.control.sampled_mono_s+1),board=replace(old,sequence=999,step=479),
            board_history=(replace(old,sequence=999,step=479),)))
        self.assertEqual(frozen.board,old)
        self.assertEqual(live.board.sequence,999)
        payload=diagnostic_payload(frozen,True)
        self.assertTrue(payload['display_frozen'])
        self.assertEqual(payload['board']['sequence'],old.sequence)
        self.assertNotIn('images',payload)
        self.assertEqual(payload['display_mode'],'SIMULATION')

    def test_all_required_states_render_at_supported_sizes(self):
        sys.path.insert(0,str(ROOT/'npu_deploy'))
        from demo_live_dashboard import demo_state
        for mode in ('running','waiting','zero','read-only','stale','finished','error','dropout','board-ok','board-warning','board-mismatch','board-stale'):
            for size in ((900,640),(1520,940)):
                for details in (False,True):
                    with self.subTest(mode=mode,size=size,details=details):
                        image,_,_=render_dashboard(demo_state(mode),*size,details=details)
                        self.assertEqual(image.size,size)
                        fpga,_,_=render_dashboard(demo_state(mode),*size,page='fpga')
                        self.assertEqual(fpga.size,size)
                        self.assertGreater(len(image.getcolors(2000000)),10)

if __name__=='__main__':unittest.main()
