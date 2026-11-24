"""Single-owner pre-dispatch authority adapter; no robot API imports.

The robot entrypoint explicitly selects this instead of the asynchronous observer.
Execution requires a matching real-board qualification report. This module never
issues a hold/reset/enable command or retries an actuator callback.
"""
import hashlib
import ipaddress
import json
import os
import math
from pathlib import Path
import sys
import time

PROJECT=Path(__file__).resolve().parents[3]
WORKSPACE=PROJECT/'fpga_integration/fpga_code/fpga_workspace'
sys.path.insert(0,str(WORKSPACE))
from host.action_authority import CheckedSender,AuthorityDenied
from host.fold_client import FoldClient
from host import fold_protocol as wire
from host.protocol import Sample,UNKNOWN,RUNNING,CHECK_CORRECTION,quantize
from host.reference import load_profile,profile_crc
from host import action_status


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def implementation_identity():
    files=[WORKSPACE/'host/action_authority.py',WORKSPACE/'host/fold_client.py',Path(__file__),
           WORKSPACE/'host/protocol.py',WORKSPACE/'host/fold_protocol.py',WORKSPACE/'host/reference.py',
           WORKSPACE/'host/fold_config.py',WORKSPACE/'config/fold_verify_profile.json',
           Path(__file__).with_name('cobot_hybrid.py'),Path(__file__).with_name('fpga_feedback_age.py'),
           Path(__file__).with_name('cobot_motion_adapter.py'),Path(__file__).with_name('policy_commissioning.py'),
           Path(__file__).with_name('cobot_connection.py'),Path(__file__).with_name('policy_exit.py')]
    runtime=PROJECT/'amd_hybrid_deploy_20261031/piper_towel_replica/runtime_source/src/lerobot'
    files += [runtime/'rollout/robot_wrapper.py',runtime/'robots/piper_dual/piper_dual.py',
              runtime/'motors/piper/piper_slave.py',runtime/'motors/piper/feedback_snapshot.py']
    files += [PROJECT/'npu_deploy'/name for name in ('hybrid_entry.py','vision_client.py','vision_worker.py','npu_provider_options.py')]
    files += [Path(__file__).with_name(name) for name in ('blend_core.py','portable_backend.py','training_image_adapter.py','training_effort_adapter.py')]
    files += [runtime/'rollout/inference'/name for name in ('sync.py','rtc.py')]
    files += [PROJECT/'npu_deploy/fpga_authority'/name for name in ('commissioning.py','qualify.py')]
    return {str(path.relative_to(PROJECT)):digest(path) for path in files}


def validate_qualified_assets(report):
    policy=Path(__file__).resolve().parent.parent
    required={str((policy/'models'/model/name).resolve()) for model in ('act050000','smol030000') for name in ('config.json','model.safetensors')}
    required.add(str((PROJECT/'npu_deploy/artifacts/smol_vision/smol_vision.onnx').resolve()))
    assets=report.get('asset_sha256')
    if not isinstance(assets,dict) or not required.issubset(assets) or any(digest(p)!=h for p,h in assets.items()):
        raise RuntimeError('Qualified model/configuration assets changed or are missing; no arm connection was made')


def verify_service(config):
    sys.path.insert(0,str(PROJECT/'npu_deploy/fpga_board'))
    from control_jupyter import request
    state=request('status');qualified=config.get('_qualification') or config['_commissioning']
    if (state.get('state')!='running' or state.get('source')!='host_observation'
            or state.get('isolated_lcd') is not True or state.get('bulk_vectors') is not True
            or not state.get('implementation_sha256')
            or state.get('implementation_sha256')!=qualified.get('board_implementation_sha256')):
        raise RuntimeError('The running board implementation differs from qualification; no arm connection was made')
    return state


def load_config(path,*,execute=False,max_actions=750,handoff_step=300,fps=30,quality_profile=None):
    path=Path(path).resolve();config=json.loads(path.read_text());profile=load_profile()
    if config.get('schema')!='fpga_action_authority_v1':raise ValueError('Unsupported FPGA authority config')
    if not math.isfinite(fps) or fps<=0:raise ValueError('Invalid controller FPS')
    if config.get('budget_scope')!='fresh_feedback_to_dispatch':raise ValueError('Dispatch budget scope must be explicit')
    for name in ('reply_budget_us','feedback_limit_us'):
        if type(config.get(name)) is not int or config[name]<=0:raise ValueError('Explicit positive '+name+' required')
    from host.fold_config import load as load_fold_profile
    if config['feedback_limit_us']>load_fold_profile()['timing']['feedback_age_us']:
        raise ValueError('Authority cannot relax the selected feedback freshness limit')
    if config['reply_budget_us']>=1e6/fps:raise ValueError('Authority budget consumes the whole nominal control period')
    if max_actions!=profile['max_actions'] or handoff_step!=profile['handoff_step']:
        raise ValueError('Controller and FPGA budgets differ')
    if config.get('expected_build_id')!=wire.BUILD_ID or config.get('guard_profile_sha256')!=digest(WORKSPACE/'config/guard_profile.json'):
        raise ValueError('Authority config does not match selected B35 guard')
    if quality_profile is not None:raise ValueError('Authority phase 1 cannot use an unqualified visual-stop profile')
    if execute:
        report_path=config.get('qualification_report')
        if not report_path:raise RuntimeError('FPGA authority is not qualified for robot execution; no arm connection was made')
        report_path=Path(report_path)
        if not report_path.is_absolute():report_path=path.parent/report_path
        report=json.loads(report_path.read_text())
        expected=dict(schema='fpga_authority_qualification_v1',passed=True,eligible_for_policy_execution=True,
                      real_pl=True,build_id=wire.BUILD_ID,implementation_sha256=implementation_identity(),
                      guard_profile_sha256=config['guard_profile_sha256'],reply_budget_us=config['reply_budget_us'],
                      feedback_limit_us=config['feedback_limit_us'],fps=fps,
                      address=config['address'],
                      boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        if any(report.get(key)!=value for key,value in expected.items()):
            raise RuntimeError('Authority qualification is stale or incomplete; no arm connection was made')
        if report.get('deadline_misses')!=0 or report.get('fault_cases_passed') is not True or report.get('full_controller_load_measured') is not True:
            raise RuntimeError('Authority timing/fault acceptance is incomplete')
        validate_qualified_assets(report)
        config['_qualification']=report
    return config,profile


def load_execution_config(path,*,execute=False,**kwargs):
    """General execution stays qualified; commissioning is an explicit bounded plan."""
    if os.environ.get('AIPC_FPGA_COMMISSIONING_PLAN') or os.environ.get('AIPC_FPGA_COMMISSIONING_SHA256'):
        from policy_commissioning import load_plan
        if not execute:raise ValueError('Commissioning plans require an explicit physical execution request')
        config,profile=load_config(path,execute=False,**kwargs)
        config['_commissioning']=load_plan(implementation_identity(),config)
        return config,profile
    return load_config(path,execute=execute,**kwargs)


def policy_target_dispatch(execute,actuator,diagnostics):
    """Select a memory receiver for read-only acceptance; never call the actuator."""
    if type(execute) is not bool:raise ValueError('Execution mode must be an explicit boolean')
    if execute:return actuator
    return lambda frozen:diagnostics.append(tuple(frozen))


def dispatch_policy_target(authority,*,feedback_reader,cpu_validator,dispatch,stop_requested,**candidate):
    """The sole required-mode policy dispatch path, injectable without robot SDKs."""
    window=authority.clock_ns()
    authority.notify('candidate',target_sequence=authority.client.sequence,step=candidate['step'])
    measured,age,sampled_ns=feedback_reader()
    cpu_validator(measured)
    return authority.send(measured=measured,feedback_age_us=age,feedback_sampled_ns=sampled_ns,
        dispatch=dispatch,stop_requested=stop_requested,
        deadline_ns=window+authority.config['reply_budget_us']*1000,**candidate)


class FpgaActionAuthority:
    def __init__(self,address,config,profile,*,client_factory=FoldClient,clock_ns=time.perf_counter_ns,log=None):
        self.address=address;self.config=config;self.guard_profile=profile;self.guard_crc=profile_crc(profile)
        self.clock_ns=clock_ns;self.client=client_factory(address,self.guard_crc,timeout=config['reply_budget_us']/1e6)
        self.gate=None;self.closed=False;self.log=log
        self.ready=False;self._channel=None;self._run_id=None
        self.status_publisher=None;self.status_mode='required';self.status_sink='robot';self.latest_status=None
        self.trial=None

    @classmethod
    def from_environment(cls,args):
        path=os.environ.get('AIPC_FPGA_AUTHORITY_CONFIG')
        if not path:return None
        if os.environ.get('AIPC_FPGA_SHADOW'):raise ValueError('Authority cannot share a board with the B32 shadow')
        config,profile=load_execution_config(path,execute=args.execute,max_actions=args.max_actions,
            handoff_step=args.handoff_step,fps=args.fps,quality_profile=args.quality_profile)
        host,sep,port=os.environ.get('AIPC_FPGA_FOLD','').rpartition(':')
        if not sep or not 1<=int(port)<=65535:raise ValueError('Authority requires an explicit B35 AIPC_FPGA_FOLD address')
        if os.environ['AIPC_FPGA_FOLD']!=config['address']:raise ValueError('Authority endpoint differs from the qualified board')
        if args.execute and os.environ.get('AIPC_FPGA_SOURCE','HOST_OBSERVATION')!='HOST_OBSERVATION':
            raise ValueError('Robot execution cannot use synthetic/replay authority source')
        if args.execute:
            config['_verified_service']=verify_service(config)
        # Verify the effective CPU envelope instead of silently accepting a CLI override.
        motion=json.loads(Path(args.motion_profile).read_text())
        if (motion['action_names']!=profile['action_names'] or quantize(motion['software_lower'])!=tuple(profile['lower'])
                or quantize(motion['software_upper'])!=tuple(profile['upper'])
                or round(args.act_joint_step_limit*1e6)!=profile['joint_step_limit']
                or round(args.act_tracking_limit*1e6)!=profile['tracking_limit']):
            raise ValueError('Effective CPU limits differ from the selected FPGA guard')
        output=PROJECT/'npu_deploy/artifacts/fpga_authority'/('run_'+str(time.time_ns()))
        output.mkdir(parents=True,exist_ok=False)
        log=(output/'authority.jsonl').open('x',buffering=1)
        try:instance=cls((str(ipaddress.IPv4Address(host)),int(port)),config,profile,log=log)
        except BaseException:log.close();raise
        if '_commissioning' in config:
            from policy_commissioning import Trial
            try:
                instance.trial=Trial(config['_commissioning']);instance.trial.validate_settings(args)
            except BaseException:instance.client.close();log.close();raise
        instance.status_mode='diagnostic' if instance.trial or not args.execute else 'required'
        instance.status_sink='robot' if args.execute else 'memory'
        instance._write('authority_created',mode=instance.status_mode,calibration_status='unconfigured',physical_can_interlock=False)
        (output/'manifest.json').write_text(json.dumps(dict(schema='fpga_authority_run_v1',execute=args.execute,
            implementation_sha256=implementation_identity(),config=config,boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            full_controller_load_measured=False,eligible_for_policy_execution=False),indent=2)+'\n')
        if instance.trial:
            print('FPGA_COMMISSIONING_PLAN='+json.dumps(dict(stage=instance.trial.plan['stage'],limits=instance.trial.limits,
                stop_mode=instance.trial.plan['stop_mode'],eligible_for_policy_execution=False)),flush=True)
        try:
            from host.action_status_publisher import Publisher
            instance.status_publisher=Publisher(instance.address,instance.client.session)
        except OSError as error:instance._write('authority_display_unavailable',error=type(error).__name__)
        return instance

    def _write(self,event,**fields):
        row=dict(fields);row.setdefault('session',self.client.session);row.update(event=event,at_ns=self.clock_ns())
        if self.log:self.log.write(json.dumps(row,allow_nan=False)+'\n')

    def start(self,*,countdown=0,stop_requested=lambda:False,sleeper=time.sleep):
        if self.ready or self.closed:raise RuntimeError('Authority already started or closed; a denial cannot be restarted')
        # A session lease is five seconds. Do not spend it on a five-second
        # operator countdown; never re-probe/retry after any candidate denial.
        if type(countdown) is not int or not 0<=countdown<=60:raise ValueError('Invalid authority startup countdown')
        for n in range(countdown,0,-1):
            if stop_requested():raise KeyboardInterrupt
            print('FPGA-authorized execution starts in '+str(n)+'...',flush=True)
            sleeper(1)
        if stop_requested():raise KeyboardInterrupt
        pose=tuple(self.guard_profile['start']);zero=(0,)*14
        probe=Sample(self.guard_crc,UNKNOWN,0,0,UNKNOWN,UNKNOWN,pose,pose,pose,pose,zero,zero)
        self.client.probe(probe)
        if self.trial:self.trial.consume()
        self.gate=CheckedSender(self.client,self.guard_profile,reply_budget_us=self.config['reply_budget_us'],
                                feedback_limit_us=self.config['feedback_limit_us'],clock_ns=self.clock_ns)
        self.ready=True
        self.notify('waiting')
        self.print_status()
        self._write('probe_accepted',build_id=wire.BUILD_ID)
        print('FPGA_AUTHORITY_READY: every policy target requires its own matching B35 reply',flush=True)

    def validate_initial(self,pose):
        if self.trial:self.trial.check_initial(pose)

    def trial_finished(self,step):return self.trial is not None and self.trial.finished(step)

    def attach_telemetry(self):
        try:
            from shared_runtime.dashboard_telemetry import channel,publish_link
            self._channel=channel();self._run_id=self._channel.control.run_id if self._channel and self._channel.control else None
            publish_link(dict(address=self.address,profile_crc=f'{self.guard_crc:08x}',controller_budget=self.guard_profile['max_actions'],
                fpga_budget=self.guard_profile['max_actions'],protocol_version=3,build_id=wire.BUILD_ID),self.client.session,self._channel,self._run_id)
            if self.latest_status:self.notify(self.latest_status['stage'])
        except ImportError:pass

    def notify(self,stage,**fields):
        if self.status_publisher is None and self._channel is None:return
        try:
            row=dict(self.latest_status or action_status.values('waiting',mode=self.status_mode,sink=self.status_sink,budget_us=self.config['reply_budget_us']))
            if fields.get('target_sequence',row['target_sequence'])!=row['target_sequence']:
                row.update(reason='',mask=action_status.UNKNOWN,round_trip_us=action_status.UNKNOWN,feedback_age_us=action_status.UNKNOWN,outcome='not_sent')
            row.update(fields);row['stage']=stage
            if stage in ('waiting','candidate','checking','allowed','denied'):row['outcome']='not_sent'
            elif stage=='dispatched':row['outcome']='sent'
            elif stage=='dispatch_unknown':row['outcome']='unknown'
            row['sent_count']=self.gate.dispatched if self.gate else 0
            action_status.validate(row);self.latest_status=row
            if self.status_publisher:self.status_publisher.publish(row)
            from shared_runtime.dashboard_telemetry import publish_authority
            publish_authority(row,self.client.session,self._channel,self._run_id)
        except Exception:pass  # A missing display receipt never changes a policy decision.

    def print_status(self):
        if self.latest_status:
            try:print('FPGA_AUTHORITY_DISPLAY='+json.dumps(dict(session=self.client.session,status=self.latest_status)),flush=True)
            except (OSError,ValueError):pass

    def send(self,*,step,elapsed_s,target,measured,previous,act,correction,previous_correction,
             blended,feedback_age_us,queue_depth,dispatch,deadline_ns,stop_requested=lambda:False,feedback_sampled_ns=None):
        if not self.ready or self.closed:raise RuntimeError('Authority is not ready; candidate was not dispatched')
        before=len(self.gate.records)
        try:
            if self.trial:
                self.trial.check_target(target,measured,previous,int(step),elapsed_s,self.guard_profile)
            if feedback_sampled_ns is not None and feedback_age_us!=UNKNOWN:
                feedback_age_us=min(UNKNOWN-1,int(feedback_age_us)+max(0,(self.clock_ns()-feedback_sampled_ns+999)//1000))
            sample=Sample(self.guard_crc,int(step),RUNNING|(CHECK_CORRECTION if blended else 0),max(0,int(elapsed_s*1e6)),
                          int(feedback_age_us),int(queue_depth),*(quantize(vector) for vector in
                          (target,measured,previous,act,correction,previous_correction)))
            self.notify('checking',target_sequence=self.client.sequence,step=int(step),feedback_age_us=sample.feedback_age_us)
            def checked_dispatch(frozen):
                self.notify('allowed',target_sequence=self.client.sequence-1,step=int(step),mask=0)
                return dispatch(frozen)
            return self.gate.send(sample,target,checked_dispatch,deadline_ns=deadline_ns,stop_requested=stop_requested)
        except AuthorityDenied:raise
        except (ValueError,TypeError,OverflowError) as error:
            if self.gate.fault is None:
                self.gate._deny('invalid_candidate:'+type(error).__name__,dict(session=self.client.session,step=int(step)))
            raise
        finally:
            if len(self.gate.records)>before:
                record=dict(self.gate.records[-1]);self._write('authority_decision',**record)
                self.notify('dispatched' if record.get('dispatched') else 'dispatch_unknown' if record.get('decision')=='DISPATCH_ERROR' else 'denied',
                    target_sequence=record.get('sequence',self.client.sequence),step=int(step),reason=('dispatch_error' if record.get('decision')=='DISPATCH_ERROR' else record.get('reason',''))[:96],
                    mask=record.get('violations',action_status.UNKNOWN),round_trip_us=min(action_status.UNKNOWN,int(record.get('round_trip_us',action_status.UNKNOWN))))
                print('FPGA_AUTHORITY_DECISION='+json.dumps(dict(record,status=self.latest_status)),flush=True)
                if self.gate.last_reply is not None and record.get('sequence')==self.gate.last_reply.sequence and 'build_id' in record:
                    values=self.gate.last_reply.values
                    try:
                        from shared_runtime.dashboard_telemetry import publish_board
                        publish_board(dict(kind='authority_sample',dispatched=record.get('dispatched',False),session=self.client.session,sequence=record['sequence'],
                            step=step,reference_match=record['reference_match'],violations=values[1],events=values[2],phase=values[3],
                            host_round_trip_us=record['round_trip_us'],board_processing_us=values[4],protocol_version=3,
                            phase_state=values[5],phase_flags=values[6],phase_cycles=values[7],build_id=values[8],event_seq=values[9],
                            verify_state=(values[6]>>8)&3,feedback_age_us=sample.feedback_age_us,calibration_status='unconfigured'),self._channel,self._run_id)
                    except ImportError:pass

    def exiting(self,reason=''):
        # Notification only: never certify that a hold actually reached a robot.
        try:
            self.notify('exiting',reason=(reason or (self.gate.fault if self.gate else '') or '')[:96])
            self.print_status()
        except Exception:pass

    def close(self,*,outcome='stopped'):
        if self.closed:return
        self.closed=True
        if self.ready:
            fault=self.gate.fault if self.gate else None
            phase='FAILED' if outcome=='error' or (fault and fault!='stop_requested') else 'ENDED' if outcome=='ended' else 'STOPPED'
            self.notify('error' if phase=='FAILED' else 'ended' if phase=='ENDED' else 'stopped',reason=(fault or ('control_error' if phase=='FAILED' else ''))[:96])
            self.print_status()
            receipt=dict(address=list(self.address),session=self.client.session,phase=phase,origin='HOST',task_result='unconfirmed',delivered=False)
            status=None
            try:
                from host.task_status import Client
                status=Client(self.address,timeout=.8,session=self.client.session)
                status.send(phase,'ERROR' if phase=='FAILED' else 'IDLE','HOST');receipt['delivered']=True
            except Exception as error:receipt['error']=str(error)
            finally:
                if status:status.close()
            print('FPGA_TASK_END='+json.dumps(receipt),flush=True)
        self.client.close()
        if self.status_publisher:self.status_publisher.close()
        if self.log:self.log.close()
