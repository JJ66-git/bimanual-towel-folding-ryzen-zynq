"""Display-only FAS1 receipts bound to the current FPG1 session and sequence.

No packet on this channel grants a motion permit or certifies robot execution.
Both desktop and LCD use presentation() for the same state meaning.
"""
import json
import struct
import zlib

HEADER=struct.Struct('<4sBBHIIQ');CRC=struct.Struct('<I');UNKNOWN=0xffffffff
STAGES=('waiting','candidate','checking','awaiting_receipt','allowed','denied','dispatched','dispatch_unknown','exiting','ended','stopped','error')
FIELDS={'mode','stage','sink','target_sequence','step','reason','feedback_age_us','round_trip_us','budget_us','mask','sent_count','outcome'}


def values(stage,*,mode='required',sink='robot',target_sequence=0,step=UNKNOWN,reason='',feedback_age_us=UNKNOWN,
           round_trip_us=UNKNOWN,budget_us=20000,mask=UNKNOWN,sent_count=0,outcome='not_sent'):
    return validate(dict(mode=mode,stage=stage,sink=sink,target_sequence=target_sequence,step=step,reason=reason,
        feedback_age_us=feedback_age_us,round_trip_us=round_trip_us,budget_us=budget_us,mask=mask,sent_count=sent_count,outcome=outcome))


def validate(row):
    if not isinstance(row,dict) or set(row)!=FIELDS:raise ValueError('authority_status_fields')
    if row['mode'] not in ('required','diagnostic','observer') or row['stage'] not in STAGES or row['sink'] not in ('robot','memory'):raise ValueError('authority_status_enum')
    if row['outcome'] not in ('not_sent','sent','unknown'):raise ValueError('authority_status_outcome')
    for key in ('target_sequence','step','feedback_age_us','round_trip_us','budget_us','mask','sent_count'):
        if type(row[key]) is not int or not 0<=row[key]<=UNKNOWN:raise ValueError('authority_status_number')
    if row['target_sequence']>=UNKNOWN or not 0<row['budget_us']<1_000_000:raise ValueError('authority_status_budget_or_sequence')
    if not isinstance(row['reason'],str) or len(row['reason'])>96:raise ValueError('authority_status_reason')
    if row['mask']!=UNKNOWN and row['mask']&~0x7ff:raise ValueError('authority_status_mask')
    if row['stage'] in ('candidate','checking','allowed','denied','waiting') and row['outcome']!='not_sent':raise ValueError('authority_status_premature_receipt')
    if row['stage']=='dispatched' and row['outcome']!='sent':raise ValueError('authority_status_missing_receipt')
    if row['stage']=='dispatch_unknown' and row['outcome']!='unknown':raise ValueError('authority_status_unknown_receipt')
    return row


def unique(pairs):
    row={}
    for key,value in pairs:
        if key in row:raise ValueError('authority_status_duplicate_field')
        row[key]=value
    return row


def pack(kind,session,sequence,tick,row):
    if kind not in (1,129) or not 1<=session<UNKNOWN or not 0<=sequence<UNKNOWN or not 0<=tick<1<<64:raise ValueError('authority_status_header')
    data=json.dumps(validate(row),ensure_ascii=True,separators=(',',':')).encode('ascii')
    if len(data)>1024:raise ValueError('authority_status_size')
    body=HEADER.pack(b'FAS1',1,kind,len(data),session,sequence,tick)+data
    return body+CRC.pack(zlib.crc32(body)&UNKNOWN)


def unpack(packet):
    if len(packet)<HEADER.size+CRC.size:raise ValueError('authority_status_short')
    magic,version,kind,size,session,sequence,tick=HEADER.unpack_from(packet)
    if magic!=b'FAS1' or version!=1 or kind not in (1,129) or not 0<size<=1024 or not 1<=session<UNKNOWN or sequence==UNKNOWN:raise ValueError('authority_status_header')
    if len(packet)!=HEADER.size+size+CRC.size or zlib.crc32(packet[:-4])&UNKNOWN!=CRC.unpack_from(packet,len(packet)-CRC.size)[0]:raise ValueError('authority_status_crc_or_length')
    try:row=validate(json.loads(packet[HEADER.size:-4].decode('ascii'),object_pairs_hook=unique))
    except (UnicodeError,json.JSONDecodeError) as error:raise ValueError('authority_status_json') from error
    return kind,session,sequence,tick,row


class Receiver:
    def __init__(self):self.identity=None;self.sequence=-1;self.tick=-1;self.latched_session=None
    def accept(self,packet,address,owner,last_sequence,source,last_mask=None):
        kind,session,sequence,tick,row=unpack(packet)
        if kind!=1 or owner is None or owner[1]!=session:raise ValueError('authority_status_session_mismatch')
        identity=(address,session)
        if self.identity and self.identity[1]==session:
            if self.identity!=identity:raise ValueError('authority_status_sender_mismatch')
            if sequence<=self.sequence or tick<=self.tick:raise ValueError('authority_status_old_receipt')
        target=row['target_sequence']
        if target<last_sequence or target>last_sequence+1:raise ValueError('authority_status_old_target')
        if row['stage'] in ('allowed','dispatched','dispatch_unknown') and (target!=last_sequence or target==0):raise ValueError('authority_status_unchecked_target')
        if row['stage'] in ('allowed','dispatched') and (last_mask!=0 or row['mask']!=0):raise ValueError('authority_status_pl_disagrees')
        if self.latched_session==session and row['stage'] not in ('denied','dispatch_unknown','exiting','ended','stopped','error'):raise ValueError('authority_status_latched')
        if source!='host_observation' and row['sink']!='memory':raise ValueError('authority_status_synthetic_robot_claim')
        if row['stage'] in ('denied','dispatch_unknown'):self.latched_session=session
        self.identity=identity;self.sequence=sequence;self.tick=tick
        return pack(129,session,sequence,tick,row),session,row


def reason_label(reason):
    if reason.startswith('latched:'):return '本轮核准已锁定'
    if reason.startswith('transport:'):return '判定链路异常'
    if reason.startswith('invalid_candidate:'):return '候选数据无效'
    return {'pl_violation':'PL 检查发现违规','no_matching_reply':'未收到匹配回包','reply_or_processing_late':'回包超过时限',
        'deadline_before_send':'送检窗口已过期','dispatch_deadline':'下发窗口已过期','stop_requested':'收到停止请求',
        'reference_mismatch':'回包与参考不一致','reply_identity':'回包身份不匹配','reply_build_or_profile':'板端配置不匹配',
        'unknown_feedback_age':'反馈年龄未知','feedback_expired_before_request':'反馈已经过期',
        'feedback_expired_before_dispatch':'下发前反馈过期','dispatch_error':'下发结果未知'}.get(reason,'核准流程中断' if reason else '等待本条结果')


def presentation(row,fresh=True):
    validate(row);stage=row['stage'];memory=row['sink']=='memory';reason=reason_label(row['reason'])
    table={'waiting':('等待候选动作','连接已核对，等待本条动作数据','等待候选','等待检查','未下发','waiting'),
        'candidate':('候选动作已生成','主机准备送检，尚未下发','候选已生成','等待送检','未下发','waiting'),
        'checking':('等待 FPGA 核准','本条候选等待匹配判定，尚未下发','数据已送检','判定中','未下发','waiting'),
        'awaiting_receipt':('等待主机回执','PL 已返回本条判定，下发状态尚未知','数据已送检','判定已返回','等待回执','waiting'),
        'allowed':('本条核准通过','已收到有效判定，等待主机下发','数据已送检','核准通过','待下发','active'),
        'denied':('本条已拦截',reason,'数据已送检','发现违规' if row['reason']=='pl_violation' else '核准失败','未下发','alert'),
        'dispatched':('测试端已接收' if memory else '主机已下发','内存接收回执，不驱动机械臂' if memory else '主机发送回执，不等于机械臂执行完成','候选已核准','核准通过','测试接收' if memory else '主机下发','active'),
        'dispatch_unknown':('下发结果未知','发送回调异常，已锁定且不自动重试','数据已送检','核准已返回','结果未知','alert'),
        'exiting':('正在受控退出','主机已请求退出，保持动作尚待确认','策略已锁定','保留判定','退出中','review'),
        'ended':('诊断流程已结束' if memory else '本轮程序已结束','诊断不认证折叠结果' if memory else '折叠结果仍待独立确认','本轮已结束','保留判定','保留回执','review'),
        'stopped':('本轮停止已请求','策略停止，机械臂物理状态未据此认证','停止请求','保留判定','保留回执','review'),
        'error':('本轮核准已中断',reason,'策略已锁定','核准中断','退出中','alert')}
    title,summary,host,fpga,dispatch,tone=table[stage]
    latched=stage in ('denied','dispatch_unknown','exiting','ended','stopped','error')
    if not fresh and not latched:title='核准数据已停更';summary='保留上次记录，等待新的会话数据';tone='history'
    return dict(headline=title,summary=summary,tone=tone,host_status=host,fpga_status=fpga,dispatch_status=dispatch,
        mode_label={'required':'逐条核准','diagnostic':'授权诊断','observer':'旁路观测'}[row['mode']],
        sink_label='内存测试' if memory else '真实下发入口',reason_label=reason,latched=latched,
        outcome_label={'not_sent':'未下发','sent':'测试端已接收' if memory else '主机已下发','unknown':'下发结果未知'}[row['outcome']])
