"""Source/freshness semantics for the live cooperative display; no device access."""
import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'fpga_integration/fpga_code/fpga_workspace'))
from screen.cooperative_state import CooperativeState
from host import fold_protocol as wire
from host.protocol import Sample,UNKNOWN
from host.reference import load_profile,profile_crc

class VisualStateTests(unittest.TestCase):
 def setUp(self):
  self.now=[1000000000];self.state=CooperativeState('host_observation',clock_ns=lambda:self.now[0],wall=lambda:1794573836)
  p=load_profile();self.crc=profile_crc(p);self.pose=tuple(p['start']);self.zeros=(0,)*14
 def frames(self,probe=False,mask=0,session=1):
  sample=Sample(self.crc,UNKNOWN if probe else 4,0 if probe else 4,0 if probe else 200000,12000,0,*([self.pose]*4),*([self.zeros]*2))
  req=wire.Frame(b'FPG1',3,1,session,0 if probe else 5,123,wire.FPG1_REQUEST.unpack(sample.pack()))
  reply=wire.Frame(b'FPG1',3,2,session,req.sequence,123,(self.crc,mask,0,3,4000,1,4,15,wire.BUILD_ID,UNKNOWN))
  return req,reply
 def accept(self,**kwargs):self.state.accepted(*self.frames(**kwargs),self.now[0],4200)
 def test_probe_never_becomes_task_or_clear_verdict(self):
  self.accept(probe=True);s=self.state.snapshot()
  self.assertEqual(s['probes'],1);self.assertEqual(s['checked_total'],0);self.assertEqual(s['headline'],'等待任务开始');self.assertIsNone(s['mask'])
 def test_host_complete_claim_cannot_create_pl_completion(self):
  self.state.host_claim({'phase':'COMPLETE','activity':'IDLE','origin':'HOST'},self.now[0]);s=self.state.snapshot()
  self.assertEqual(s['phase'],0);self.assertEqual(s['verify'],0);self.assertEqual(s['checked_total'],0)
 def test_matched_task_drives_real_metrics_then_expires(self):
  self.accept();self.state.pl(1,0,1,0,UNKNOWN,15,0);s=self.state.snapshot()
  self.assertEqual(s['headline'],'已识别张爪');self.assertTrue(s['fresh']);self.assertEqual(s['feedback_age_us'],12000);self.assertAlmostEqual(s['pl_compute_us'],.3)
  self.now[0]+=201000000;s=self.state.snapshot()
  self.assertFalse(s['fresh']);self.assertEqual(s['headline'],'等待新数据');self.assertEqual(s['checked_total'],1)
 def test_violation_is_never_rendered_as_clear(self):
  self.accept(mask=1<<3);s=self.state.snapshot();self.assertEqual(s['tone'],'alert');self.assertEqual(s['headline'],'动作数据有异常');self.assertIn('实际位置偏离目标',s['violations'])
 def test_new_session_resets_run_only(self):
  self.accept();self.accept();self.accept(session=2);s=self.state.snapshot()
  self.assertEqual(s['checked_total'],3);self.assertEqual(s['run_samples'],1);self.assertEqual(len(s['history']),1)
 def test_autonomous_pl_result_enters_event_history(self):
  self.state.pl(3,1,5,1,9,15,0);self.state.pl(3,3,8,0,9,15,0);s=self.state.snapshot()
  self.assertEqual(s['evidence_label'],'暂时无法确认');self.assertEqual(s['events'][0]['title'],'暂时无法确认')
 def test_wrong_event_cannot_replace_current_consumed_frame(self):
  request=wire.Frame(b'FVE1',1,1,1,9,123,(100,7,4290,320,240,9000,1500,1))
  wrong=wire.Frame(b'FVE1',1,2,1,9,123,(7,1,2,1));self.state.accepted(request,wrong,self.now[0],30)
  self.assertIsNone(self.state.snapshot()['frame_id']);self.assertEqual(self.state.snapshot()['verify'],0)
 def test_bounded_histories_and_reject_does_not_count_task(self):
  for _ in range(100):self.accept();self.state.rejected('bad_crc',self.now[0])
  s=self.state.snapshot();self.assertEqual(len(s['history']),48);self.assertEqual(len(s['events']),16);self.assertEqual(s['checked_total'],100);self.assertEqual(s['rejected'],100)
 def test_synthetic_result_always_exposes_source(self):
  state=CooperativeState('synthetic_test');s=state.snapshot();self.assertTrue(s['is_synthetic']);self.assertEqual(s['source_label'],'合成测试')
 def test_new_task_clears_prior_frame_and_event_history(self):
  self.accept();self.state.update(frame_id=77,frame_center=[1,2],frame_area_bp=4290)
  self.accept(session=2);s=self.state.snapshot();self.assertIsNone(s['frame_id']);self.assertIsNone(s['frame_center']);self.assertEqual(s['events'][0]['kind'],'session')
 def test_repeated_same_violation_is_one_meaningful_event(self):
  self.accept(mask=4);self.accept(mask=4);s=self.state.snapshot();self.assertEqual(s['flagged_total'],2);self.assertEqual(sum(e['kind']=='violation' for e in s['events']),1)
 def test_pl_reset_clears_stale_visual_frame(self):
  self.state.update(frame_id=44);self.state.pl(0,0,1,0,UNKNOWN,0,0,UNKNOWN,0);self.assertIsNone(self.state.snapshot()['frame_id'])
 def test_cooperation_uses_one_correlated_transaction(self):
  self.accept();s=self.state.snapshot();c=s['cooperation']
  for key in ['host_proof','fpga_proof','display_proof']:self.assertIn('第5条',c[key])
  self.assertEqual(c['host_status'],'数据已送检');self.assertEqual(c['fpga_status'],'已经检查')
 def test_cooperation_does_not_fake_activity_for_probe_or_stale(self):
  self.accept(probe=True);self.assertEqual(self.state.snapshot()['cooperation']['fpga_status'],'准备检查')
  self.accept();self.now[0]+=201000000;c=self.state.snapshot()['cooperation'];self.assertEqual(c['fpga_status'],'上次结果');self.assertEqual(c['display_status'],'保留结果')
 def test_invalid_feedback_never_claims_current_phase(self):
  request,reply=self.frames();reply=wire.Frame(reply.magic,reply.version,reply.kind,reply.session,reply.sequence,reply.tick_us,(self.crc,0,0,3,4000,2,0,15,wire.BUILD_ID,UNKNOWN))
  self.state.accepted(request,reply,self.now[0],4200);self.state.pl(2,0,1,0,UNKNOWN,15,0)
  snapshot=self.state.snapshot();self.assertTrue(snapshot['fresh']);self.assertFalse(snapshot['phase_current']);self.assertEqual(snapshot['headline'],'等待有效状态')
 def terminal(self,phase='ENDED',origin='HOST',session=1):
  self.state.host_claim(dict(phase=phase,activity='ERROR' if phase=='FAILED' and origin=='HOST' else 'IDLE',origin=origin),self.now[0],session)
 def test_ended_run_is_retained_without_claiming_physical_success(self):
  self.accept();self.terminal();self.now[0]+=10000000000;s=self.state.snapshot()
  self.assertEqual(s['headline'],'本轮运行已结束');self.assertEqual(s['task_result'],'unconfirmed')
  self.assertEqual(s['evidence_label'],'结果待现场确认');self.assertIn('本轮已结束',s['activity_line'])
 def test_host_complete_is_only_a_run_end_even_with_matching_session(self):
  self.accept();self.terminal('COMPLETE');s=self.state.snapshot()
  self.assertEqual(s['task_result'],'unconfirmed');self.assertNotIn('成功',s['headline'])
 def test_failure_and_stop_are_distinct_from_physical_fold_outcome(self):
  self.accept()
  for phase,headline in [('FAILED','本轮执行中断'),('STOPPED','本轮运行已停止')]:
   self.terminal(phase);s=self.state.snapshot();self.assertEqual(s['headline'],headline);self.assertEqual(s['task_result'],'unconfirmed')
 def test_operator_confirmation_requires_current_task_and_clears_on_new_run(self):
  self.accept();self.terminal('COMPLETE','OPERATOR',2);self.assertEqual(self.state.snapshot()['task_result'],'unconfirmed')
  self.terminal('COMPLETE','OPERATOR');s=self.state.snapshot()
  self.assertEqual(s['task_result'],'success');self.assertEqual(s['task_result_source'],'operator')
  self.terminal('FAILED','OPERATOR');self.assertEqual(self.state.snapshot()['task_result'],'failure')
  self.accept(session=2);s=self.state.snapshot();self.assertEqual(s['task_result'],'unconfirmed');self.assertIsNone(s['task_lifecycle'])
 def test_uncalibrated_pl_verify_never_claims_physical_success(self):
  self.accept();self.state.pl(3,2,0,3,9,15,0,44,1);s=self.state.snapshot()
  self.assertFalse(s['visual_success_confirmed']);self.assertEqual(s['task_result'],'unconfirmed')
  self.assertEqual(s['evidence_label'],'视觉结果待核验');self.assertNotIn('成功',s['headline'])
 def test_failure_after_new_probe_cannot_leave_the_previous_success_on_screen(self):
  self.accept();self.terminal('COMPLETE','OPERATOR');self.assertEqual(self.state.snapshot()['task_result'],'success')
  self.accept(probe=True,session=2);s=self.state.snapshot()
  self.assertEqual(s['headline'],'等待任务开始');self.assertEqual(s['task_result'],'unconfirmed')
  self.assertEqual(s['run_samples'],0);self.assertEqual(s['checked_total'],1);self.assertIsNone(s['mask'])
  self.terminal('FAILED',session=2);s=self.state.snapshot()
  self.assertEqual(s['headline'],'本轮执行中断');self.assertEqual(s['task_result'],'unconfirmed')
if __name__=='__main__':unittest.main()
