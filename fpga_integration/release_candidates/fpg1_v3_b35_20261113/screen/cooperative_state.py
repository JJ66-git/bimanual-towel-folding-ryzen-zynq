"""Event-driven display state. Observes validated packets and PL reads; no hardware writes."""
from collections import deque
import math
import time
from host import fold_protocol as wire
from host import action_status
from host import task_status

UNKNOWN=0xffffffff
REASONS=('目标位置超范围','ACT目标超范围','关节变化过大','实际位置偏离目标','模型修正过大','修正变化过快','夹爪来源不符','起始位置不符','任务步数超限','机械臂状态过旧','两端配置不一致')
VERIFY_REASONS=('连续画面符合设定条件','等待双臂退开','画面不属于本轮检查','画面太旧，等待新画面','这张画面已检查过','再等几张稳定画面','画面不清楚或形态不符','双臂位置变化，需重新确认','等待太久，未能确认结果','本轮检查已结束')
PHASE_NAMES=('等待任务','已识别张爪','已识别夹持','已识别翻折')

class CooperativeState:
 def __init__(self,source,clock_ns=time.monotonic_ns,wall=time.time):
  self.clock_ns=clock_ns;self.wall=wall;self.started_ns=clock_ns();self.history=deque(maxlen=48);self.events=deque(maxlen=16)
  self.s={'source':source,'phase':0,'verify':0,'reason':1,'stable':0,'event':UNKNOWN,'cycles':None,
   'samples':0,'checked_total':0,'run_samples':0,'flagged_total':0,'rejected':0,'probes':0,'vision_packets':0,'queries':0,'host_claims':0,
   'session':None,'sequence':None,'step':None,'sample_ns':None,'packet_ns':None,'host_claim_ns':None,'vision_ns':None,
   'mask':None,'phase_sample_valid':False,'feedback_limit_us':100000,'feedback_age_us':None,'ps_service_us':None,'phase_cycles':None,'frame_id':None,'host_task':None,
   'measured':None,'target':None,'build_id':wire.BUILD_ID,'fclk_hz':50000000,'phase_config_crc':None,'task_lifecycle':None,
   'config_matched':False,'calibration_status':'unknown','watchdog_control':None,'event_started_ns':None,
   'image_width':640,'image_height':480,'frame_center':None,'frame_area_bp':None,'min_frames':3,'max_actions':750,'revision':0,
   'authority':None,'authority_ns':None,'authority_claims':0,'maintenance':None}
  self._pl_signature=None
 def _event(self,kind,title,detail='',at=None):
  at=self.clock_ns() if at is None else at
  self.events.append({'kind':kind,'title':title,'detail':detail,'mono_ns':at})
 def update(self,**fields):
  self.s.update(fields)
 def pl(self,phase,verify,reason,stable,event,cycles,watchdog_control=None,frame_id=None,event_session=None):
  signature=(phase,verify,reason,stable,event)
  previous=self._pl_signature;self._pl_signature=signature
  self.s.update(phase=phase,verify=verify,reason=reason,stable=stable,event=event,cycles=cycles,watchdog_control=watchdog_control)
  if frame_id is not None:self.s['frame_id']=None if frame_id==UNKNOWN else frame_id
  if event_session is not None:self.s['event_session']=event_session
  if verify==0 and event==UNKNOWN:self.s.update(frame_id=None,frame_center=None,frame_area_bp=None,event_started_ns=None)
  if previous is not None and previous!=signature:
   if phase!=previous[0]:self._event('phase',PHASE_NAMES[phase] if phase<4 else '未知阶段','FPGA识别到阶段变化')
   if verify!=previous[1]:
    titles=('等待双臂退开','双臂已退开，开始检查','视觉状态已返回','暂时无法确认')
    self._event('evidence',titles[verify] if verify<4 else '未知复核状态',VERIFY_REASONS[reason] if reason<len(VERIFY_REASONS) else '未知原因')
   if verify==1 and (previous[1]!=1 or event!=previous[4]):self.s['event_started_ns']=self.clock_ns()
   self.s['revision']+=1
 def host_claim(self,values,at,session=None):
  self.s.update(host_task=dict(values),host_claim_ns=at,packet_ns=at,host_claims=self.s['host_claims']+1)
  if values['phase'] in task_status.RESET_PHASES:
   self.s['maintenance']=dict(values,session=session,received_ns=at)
   self.s['revision']+=1
   return
  terminal=values['phase'] in ('ENDED','FAILED','STOPPED','COMPLETE') or values['activity']=='ERROR'
  if terminal and session is not None and session==self.s['session']:
   self.s['task_lifecycle']=dict(values,session=session,received_ns=at)
   self._event('run_status','现场结果确认' if values['origin']=='OPERATOR' else '主机运行结束',values['phase'],at)
  self.s['revision']+=1
 def authority_claim(self,values,at,session):
  action_status.validate(values)
  if session!=self.s['session']:raise ValueError('authority_display_session_mismatch')
  self.s.update(authority=dict(values),authority_ns=at,packet_ns=at,authority_claims=self.s['authority_claims']+1)
  self.s['revision']+=1
  if values['stage'] in ('denied','dispatch_unknown','error'):
   self._event('authority','本条策略已锁定',action_status.reason_label(values['reason']),at)
 def _new_session(self,session,at):
  self.s.update(session=session,task_lifecycle=None,run_samples=0,sequence=None,step=None,sample_ns=None,
   mask=None,feedback_age_us=None,phase_sample_valid=False,phase_cycles=None,ps_service_us=None,measured=None,target=None,
   phase=0,verify=0,reason=1,stable=0,event=UNKNOWN,event_session=None,frame_id=None,
   frame_center=None,frame_area_bp=None,vision_ns=None,event_started_ns=None,authority=None,authority_ns=None,maintenance=None)
  self._pl_signature=None;self.history.clear();self.events.clear()
  self._event('session','新的协作会话','会话 %08X'%session,at)
 def accepted(self,request,reply,at,service_us):
  self.s['packet_ns']=at;self.s['revision']+=1
  if request.magic==b'FPG1':
   if self.s['session']!=reply.session:self._new_session(reply.session,at)
   if request.values[1]==UNKNOWN:
    self.s['probes']+=1;return
   self.s['maintenance']=None
   values=reply.values
   receipt=self.s.get('authority')
   if receipt and receipt['stage'] not in ('denied','dispatch_unknown','error','exiting','ended','stopped') and (receipt['target_sequence']<reply.sequence or receipt['stage'] in ('candidate','checking')):
    self.s.update(authority=action_status.values('awaiting_receipt',mode=receipt['mode'],sink=receipt['sink'],
     target_sequence=reply.sequence,step=request.values[1],mask=values[1],outcome='unknown',sent_count=receipt['sent_count']),authority_ns=at)
   previous_mask=self.s['mask']
   self.s.update(session=reply.session,sequence=reply.sequence,step=request.values[1],sample_ns=at,
    samples=self.s['samples']+1,checked_total=self.s['checked_total']+1,run_samples=self.s['run_samples']+1,
    flagged_total=self.s['flagged_total']+int(values[1]!=0),mask=values[1],
    feedback_age_us=None if request.values[4]==UNKNOWN else request.values[4],ps_service_us=service_us,
    phase_cycles=values[7],phase_sample_valid=bool(values[6]&4),measured=list(request.values[20:34]),target=list(request.values[6:20]))
   self.history.append({'mono_ns':at,'ps_service_us':service_us,'feedback_age_us':self.s['feedback_age_us'],
     'phase_cycles':values[7],'step':request.values[1],'mask':values[1]})
   if values[1] and values[1]!=previous_mask:self._event('violation','FPGA发现动作数据异常',' / '.join(name for bit,name in enumerate(REASONS) if values[1]&(1<<bit)),at)
   elif previous_mask and values[1]==0:self._event('recovered','输入检查恢复','这条动作数据未发现异常',at)
  elif request.magic==b'FVE1':
   if request.kind==3:self.s['queries']+=1;return
   self.s['vision_packets']+=1
   # A wrong-event or duplicate reply does not establish a new consumed frame.
   if reply.values[2] not in (2,3,4,8,9):
    self.s.update(frame_id=reply.values[0],vision_ns=at,frame_center=[request.values[3],request.values[4]],frame_area_bp=request.values[2])
 def rejected(self,reason,at):
  self.s['rejected']+=1;self.s['revision']+=1;self._event('rejected','已拒绝无效数据',str(reason),at)
 def snapshot(self):
  now=self.clock_ns();s=dict(self.s)
  def age(stamp):return None if stamp is None else max(0,(now-stamp)/1e6)
  s['schema']='cooperative_display_v1';s['ui_revision']='cooperative_ui_20261113_1'
  s.update(packet_age_ms=age(s['packet_ns']),sample_age_ms=age(s['sample_ns']),vision_age_ms=age(s['vision_ns']),host_claim_age_ms=age(s['host_claim_ns']),
   uptime_s=(now-self.started_ns)/1e9,board_epoch_s=self.wall(),snapshot_monotonic_ns=now)
  s['fresh']=s['sample_age_ms'] is not None and s['sample_age_ms']<=200
  s['phase_current']=bool(s['fresh'] and s['phase_sample_valid'] and s['feedback_age_us'] is not None and s['feedback_age_us']<=s['feedback_limit_us'])
  s['visual_success_confirmed']=bool(s['verify']==2 and s['phase_current'] and s['source']=='host_observation'
   and s['calibration_status']=='field_validated' and s['config_matched'] and s['mask']==0 and s['reason']==0 and s.get('event_session')==s['session']
   and s.get('frame_id') is not None and s['stable']>=s['min_frames'])
  s['task_result']='unconfirmed';s['task_result_source']=None
  s['host_connected']=s['packet_age_ms'] is not None and s['packet_age_ms']<=5000
  s['source_label']={'synthetic_test':'合成测试','recorded_replay':'记录回放','host_observation':'主机观测'}.get(s['source'],'未知来源')
  s['is_synthetic']=s['source']=='synthetic_test'
  s['history']=[dict(row,age_ms=max(0,(now-row['mono_ns'])/1e6)) for row in self.history]
  s['events']=[dict(row,age_s=max(0,(now-row['mono_ns'])/1e9)) for row in reversed(self.events)]
  s['violations']=[name for bit,name in enumerate(REASONS) if s['mask'] is not None and s['mask']&(1<<bit)]
  s['phase_label']=PHASE_NAMES[s['phase']] if s['phase']<4 else '未知阶段'
  s['reason_label']=VERIFY_REASONS[s['reason']] if s['reason']<len(VERIFY_REASONS) else '未知原因'
  if not s['run_samples']:
   s.update(headline='等待任务开始',summary='电脑已接通，等待动作数据' if s['host_connected'] else '等待电脑发送动作数据',tone='waiting',guard_label='等待动作数据')
  elif not s['fresh']:
   s.update(headline='等待新数据',summary='下方为上次结果，等待新的动作数据',tone='history',guard_label='上次数据有异常' if s['mask'] else '上次未见异常')
  elif s['mask']:
   s.update(headline='动作数据有异常',summary='FPGA发现这条动作数据不符合设置',tone='alert',guard_label='动作数据有异常')
  elif not s['phase_current']:
   s.update(headline='等待有效状态',summary='这条状态未用于判断阶段',tone='waiting',guard_label='等待有效状态')
  else:
   headline=s['phase_label'] if s['phase'] else '正在检查动作'
   if s['verify']==1:headline='正在检查折叠'
   elif s['visual_success_confirmed']:headline='视觉复核通过'
   elif s['verify']==2:headline='动作阶段已识别'
   elif s['verify']==3:headline='暂时无法确认'
   s.update(headline=headline,summary='输入为模拟数据，FPGA实际检查' if s['is_synthetic'] else 'FPGA根据两臂状态识别当前阶段',tone='active',guard_label='未发现数据异常')
  s['evidence_label']=('等待双臂退开','正在检查折叠','视觉结果待核验','暂时无法确认')[min(s['verify'],3)]
  if s['visual_success_confirmed']:
   s.update(task_result='success',task_result_source='field_validated_vision',evidence_label='视觉复核通过')
  s['evidence_note']='相机检查还需现场标定' if s['source']=='host_observation' and s['calibration_status']!='field_validated' else s['reason_label']
  s['pl_compute_us']=None if s['phase_cycles'] is None else s['phase_cycles']*1e6/s['fclk_hz']
  s['step_progress']=None if s['step'] is None else min(1,max(0,(s['step']+1)/s['max_actions']))
  s['event_elapsed_ms']=age(s['event_started_ns']) if s['verify']==1 else None
  if s['sequence'] is None:
   s['cooperation']={'summary':'电脑已接通，等待动作数据' if s['host_connected'] else '等待电脑发送动作数据',
    'host_status':'连接已通' if s['host_connected'] else '等待动作','fpga_status':'准备检查','display_status':'等待结果',
    'host_proof':'已验证连接，尚未收到动作数据' if s['host_connected'] else '尚未收到可供检查的动作数据',
    'fpga_proof':'配置已经核对，正式动作检查尚未开始','display_proof':'板屏显示等待状态；本页来自同一份板端快照'}
  else:
   number=s['sequence'];prefix='本轮' if s['fresh'] else '上次'
   s['cooperation']={'summary':('电脑第%d条，FPGA已发回检查结果'%number) if s['fresh'] else '等待新数据，保留上次协作结果',
    'host_status':'数据已送检' if s['fresh'] else '等待更新',
    'fpga_status':('发现问题' if s['mask'] else '已经检查') if s['fresh'] else '上次结果',
    'display_status':'板屏已显示' if s['fresh'] else '保留结果',
    'host_proof':'%s第%d条动作数据，包含两臂14项状态'%(prefix,number),
    'fpga_proof':'已检查第%d条，并发回结果'%number,
    'display_proof':'%s第%d条结果，来自实际板屏快照'%(prefix,number)}

  lifecycle=s.get('task_lifecycle')
  if lifecycle and lifecycle['session']==s['session']:
   s.update(fresh=False,phase_current=False)
   operator=lifecycle['origin']=='OPERATOR'
   phase=lifecycle['phase']
   if operator and phase=='COMPLETE':
    s.update(headline='现场确认成功',summary='操作员已确认本轮折叠成功',tone='confirmed',task_result='success',task_result_source='operator')
   elif operator and phase=='FAILED':
    s.update(headline='现场确认未完成',summary='操作员已确认本轮任务未完成',tone='alert',task_result='failure',task_result_source='operator')
   elif phase=='STOPPED':
    s.update(headline='本轮运行已停止',summary='已停止执行，折叠结果待现场确认',tone='review')
   elif phase=='FAILED' or lifecycle['activity']=='ERROR':
    s.update(headline='本轮执行中断',summary='程序异常结束，折叠结果待现场确认',tone='alert')
   else:
    s.update(headline='本轮运行已结束',summary='动作序列结束，折叠结果待现场确认',tone='review')
    if s['visual_success_confirmed']:
     s.update(headline='视觉复核通过',summary='本轮运行已结束，已标定视觉复核通过',tone='confirmed')
   if s['task_result_source']=='operator':
    s['evidence_label']='现场确认：成功' if s['task_result']=='success' else '现场确认：未完成'
    s['evidence_note']='结果来源：操作员现场确认'
   elif not s['visual_success_confirmed']:
    s['evidence_label']='结果待现场确认';s['evidence_note']='动作规则通过不代表折叠成功'
   s['activity_line']='本轮已结束 · 不再等待动作数据'
   s['cooperation'].update(summary='本轮运行已结束，保留本轮结果',host_status='本轮已结束',
     fpga_status='已保留检查',display_status='显示本轮结果')

  authority=s.get('authority')
  if authority:
   age_ms=age(s['authority_ns']);receipt_fresh=age_ms is not None and age_ms<=1500
   if authority['stage']=='allowed':receipt_fresh=receipt_fresh and s['sample_age_ms'] is not None and s['sample_age_ms']<authority['budget_us']/1000
   projection=action_status.presentation(authority,receipt_fresh)
   s['authority_presentation']=projection;s['authority_age_ms']=age_ms
   s.update(headline=projection['headline'],summary=projection['summary'],tone=projection['tone'])
   if authority['sink']=='memory':
    s.update(task_result='unconfirmed',task_result_source=None,evidence_label='测试不认证折叠结果',evidence_note='输入与回执仅用于诊断')
   elif s['task_result_source']=='operator':
    s.update(headline='现场确认成功' if s['task_result']=='success' else '现场确认未完成',summary='结果来源：操作员现场确认')
   s['activity_line']=projection['mode_label']+' · '+projection['sink_label']
   s['cooperation'].update(summary='第%d条 · %s · %s'%(authority['target_sequence'],projection['mode_label'],projection['outcome_label']),
    host_status=projection['host_status'],fpga_status=projection['fpga_status'],display_status=projection['dispatch_status'],
    display_name='下发状态',host_proof='主机候选与会话 %08X 关联'%s['session'],
    fpga_proof='本条核准与下发回执分别记录',display_proof=projection['outcome_label']+'；来源为主机回执，非机械臂执行确认')
   if authority['stage'] in ('denied','dispatch_unknown','error'):
    s['guard_label']=projection['reason_label'];s['evidence_note']='策略已锁定；折叠结果仍未确认'
  maintenance=s.get('maintenance')
  if maintenance:
   phase=maintenance['phase'];stale=phase=='RESETTING' and age(maintenance['received_ns'])>2500
   title,note,tone={
    'RESETTING':('双臂正在复位','主机执行慢速复位，请观察机械臂','active'),
    'RESET_DONE':('复位流程已结束','主机报告到位，等待下一轮任务','review'),
    'RESET_FAILED':('复位流程已中断','主机报告异常，未启动后续任务','alert'),
    'RESET_STOPPED':('复位已停止','已收到停止请求，未启动后续任务','review')}[phase]
   if stale:title,note,tone='复位状态已停更','等待主机更新，请核对机械臂状态','history'
   s.update(headline=title,summary=note,tone=tone,fresh=False,phase_current=False,
    task_result='unconfirmed',task_result_source=None,visual_success_confirmed=False,
    evidence_label='复位不认证折叠结果',evidence_note='复位状态来源：主机报告',
    activity_line='仅复位 · 未启动折叠策略',guard_label='复位沿用原生控制',authority_presentation=None,
    authority=None,phase=0,verify=0,stable=0,mask=None,violations=[],feedback_age_us=None,
    pl_compute_us=None,ps_service_us=None,run_samples=0)
   s['cooperation'].update(summary='仅复位 · 状态来源：主机报告',host_status='复位已中断' if phase=='RESET_FAILED' else '原生复位',
    fpga_status='显示复位状态',display_status='等待任务',display_name='下发状态')
  return s
