"""One-use, bounded field trials; never manufactures a production qualification.

The model, smoothing, timing and native limits remain unchanged. A reviewed
plan adds stricter count/time/pose limits and a controlled-stop option. A plan
cannot authorize general policy execution or certify physical/folding success.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import time

STAGES={
    'small':dict(steps=5,max_joint_excursion_rad=.30,max_joint_step_rad=.15,max_gripper_excursion_m=.010,max_run_s=10),
    'handoff':dict(steps=350,max_joint_excursion_rad=2.7,max_joint_step_rad=1.0,max_gripper_excursion_m=.080,max_run_s=120),
    'full':dict(steps=750,max_joint_excursion_rad=2.7,max_joint_step_rad=1.0,max_gripper_excursion_m=.080,max_run_s=180)}
JOINTS=tuple(i for i in range(14) if i not in (6,13))
IGNORED_SETTINGS={'trace_file','capture_refills','backend_device'}


def settings(args):
    return {k:str(v.resolve()) if isinstance(v,Path) else v for k,v in vars(args).items() if k not in IGNORED_SETTINGS}


def file_hash(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def usb_serial(device):
    device=Path(device).resolve(strict=True)
    for path in (device,*device.parents):
        if (path/'serial').is_file():return (path/'serial').read_text().strip()
    raise ValueError('USB serial unavailable')


def bindings_match(bindings):
    if not isinstance(bindings,dict) or len(bindings)!=5:return False
    return all(usb_serial(path)==serial for path,serial in bindings.items())


def requested():
    plan=os.environ.get('AIPC_FPGA_COMMISSIONING_PLAN');digest=os.environ.get('AIPC_FPGA_COMMISSIONING_SHA256')
    if bool(plan)!=bool(digest):raise ValueError('Commissioning requires its reviewed plan and exact SHA-256')
    return bool(plan)


def load_plan(identity,config,*,now_ns=None,check_bindings=bindings_match):
    if not requested():raise ValueError('Explicit reviewed commissioning plan required')
    path=Path(os.environ['AIPC_FPGA_COMMISSIONING_PLAN']).resolve();raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=os.environ['AIPC_FPGA_COMMISSIONING_SHA256']:
        raise ValueError('Commissioning plan changed after review')
    p=json.loads(raw);now_ns=time.perf_counter_ns() if now_ns is None else now_ns
    if (p.get('schema')!='fpga_policy_commissioning_plan_v1' or p.get('stage') not in STAGES
            or p.get('limits')!=STAGES[p['stage']] or p.get('implementation_sha256')!=identity
            or p.get('boot_id')!=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            or p.get('address')!=config['address'] or p.get('guard_profile_sha256')!=config['guard_profile_sha256']
            or p.get('reply_budget_us')!=config['reply_budget_us'] or p.get('feedback_limit_us')!=config['feedback_limit_us']
            or not p.get('board_implementation_sha256') or p.get('eligible_for_policy_execution') is not False):
        raise ValueError('Stale or incomplete commissioning plan')
    if (type(p.get('created_ns')) is not int or type(p.get('expires_ns')) is not int
            or not p['created_ns']<=now_ns<p['expires_ns']<=p['created_ns']+900_000_000_000):
        raise ValueError('Commissioning plan expired')
    anchor=p.get('anchor')
    if (not isinstance(anchor,list) or len(anchor)!=14
            or any(type(v) not in (float,int) or not math.isfinite(v) for v in anchor)):
        raise ValueError('Invalid commissioning start pose')
    if p.get('stop_mode') not in ('limit','operator_stop','pl_veto','reply_loss'):raise ValueError('Invalid commissioned stop mode')
    if not check_bindings(p.get('device_bindings')):raise ValueError('Commissioning device bindings changed')
    if not p.get('asset_sha256') or any(file_hash(f)!=digest for f,digest in p['asset_sha256'].items()):
        raise ValueError('Commissioning assets changed')
    if path.with_suffix('.consumed').exists():raise ValueError('Commissioning plan was already consumed')
    return dict(p,_path=str(path))


class Trial:
    def __init__(self,plan,*,clock_ns=time.perf_counter_ns):
        self.plan=plan;self.limits=plan['limits'];self.clock_ns=clock_ns;self.anchor=tuple(plan['anchor'])
        self.consumed=False;self.initial_checked=False;self.started_ns=None

    def validate_settings(self,args):
        if settings(args)!=self.plan['control_settings']:raise ValueError('Effective controller settings differ from reviewed trial')
        if not args.execute or args.resume_current or args.act_only or args.quality_profile is not None:
            raise ValueError('Trial requires the original hybrid start and no unqualified visual stop')
        if float(os.environ.get('PIPER_ACTION_FILTER_ALPHA','1'))!=1:raise ValueError('Unreviewed target filtering')

    def consume(self):
        now=self.clock_ns()
        if self.consumed or not self.plan['created_ns']<=now<self.plan['expires_ns']:raise ValueError('Trial expired or consumed')
        with Path(self.plan['_path']).with_suffix('.consumed').open('x') as out:
            out.write(json.dumps(dict(consumed_ns=now,pid=os.getpid()))+'\n')
        self.consumed=True

    def check_initial(self,pose):
        if not self.consumed:raise ValueError('Trial must be consumed before connecting for motion')
        if len(pose)!=14 or not all(math.isfinite(v) for v in pose):raise ValueError('Invalid trial initial feedback')
        for i in range(14):
            if abs(pose[i]-self.anchor[i])>(.02 if i in JOINTS else .004):
                raise ValueError('Pose moved since trial review; no connection hold or enable sent')
        self.initial_checked=True

    def finished(self,step):return step>=self.limits['steps']

    def check_target(self,target,measured,previous,step,elapsed_s,profile):
        if not self.initial_checked or not self.consumed:raise ValueError('Trial initial feedback not verified')
        if (type(step) is not int or step<0 or self.finished(step) or not math.isfinite(elapsed_s)
                or not 0<=elapsed_s<=self.limits['max_run_s']):raise ValueError('Trial count or time exceeded')
        if self.started_ns is None:self.started_ns=self.clock_ns()
        if (self.clock_ns()-self.started_ns)/1e9>self.limits['max_run_s']:raise ValueError('Trial wall duration exceeded')
        if any(len(v)!=14 or not all(math.isfinite(x) for x in v) for v in (target,measured,previous)):
            raise ValueError('Invalid trial candidate or feedback')
        for i in range(14):
            low,high=profile['lower'][i]/1e6,profile['upper'][i]/1e6
            if not low-1e-6<=target[i]<=high+1e-6:raise ValueError('Trial target outside original guard')
            limit=self.limits['max_joint_excursion_rad'] if i in JOINTS else self.limits['max_gripper_excursion_m']
            if abs(target[i]-self.anchor[i])>limit or abs(measured[i]-self.anchor[i])>limit:
                raise ValueError('Trial pose envelope exceeded')
            if i in JOINTS and abs(target[i]-previous[i])>self.limits['max_joint_step_rad']:
                raise ValueError('Trial per-target joint step exceeded')

    def inject_fault(self,authority,feedback_reader,elapsed_s):
        """Real PL diagnostic copy, non-actuating canary; original exit handles denial."""
        from host.protocol import Sample,RUNNING,quantize
        from host.action_authority import AuthorityDenied
        mode=self.plan['stop_mode']
        if mode not in ('pl_veto','reply_loss'):raise ValueError('Fault was not included in reviewed plan')
        if authority.gate.dispatched!=self.limits['steps']:raise ValueError('Unexpected trial dispatch count')
        window=authority.clock_ns();pose,age,sampled_ns=feedback_reader();target=list(pose)
        if mode=='pl_veto':target[8]=2.65
        age+=max(0,(authority.clock_ns()-sampled_ns+999)//1000)
        q=quantize(pose);bad=quantize(target);zero=(0,)*14
        sample=Sample(authority.guard_crc,self.limits['steps'],RUNNING,max(0,int(elapsed_s*1e6)),age,0,bad,q,q,bad,zero,zero)
        original=authority.client.send_sample
        def drop(candidate):original(candidate);return None
        def canary(_):raise RuntimeError('A diagnostic fault unexpectedly reached the canary; no robot callback exists')
        if mode=='reply_loss':authority.client.send_sample=drop
        try:
            authority.gate.send(sample,target,canary,deadline_ns=window+authority.config['reply_budget_us']*1000)
        except AuthorityDenied as error:
            authority._write('commissioning_fault',injected=mode,**error.record)
            authority.notify('denied',target_sequence=error.record.get('sequence',authority.client.sequence),
                step=self.limits['steps'],reason=error.reason,mask=error.record.get('violations',0xffffffff))
            expected='pl_violation' if mode=='pl_veto' else 'no_matching_reply'
            if error.reason!=expected:raise RuntimeError('Commissioning fault did not produce expected denial') from error
            raise
        else:raise RuntimeError('Commissioning fault was not rejected')
        finally:authority.client.send_sample=original
