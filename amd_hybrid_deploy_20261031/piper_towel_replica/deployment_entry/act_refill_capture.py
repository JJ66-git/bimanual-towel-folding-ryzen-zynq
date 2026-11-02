"""Observational ACT refill recorder. No controller, camera or policy decisions.

During motion: copy existing frames/tensors into memory, without CPU image
encoding or file writes. finish() is called after the original exit cleanup.
Original model exceptions propagate; observer errors are recorded, not turned
into new motion gates. send_action returning is NOT actuator acknowledgement.
"""
import copy
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import torch

ROLES=('left','middle','right')
ARM=[0,1,2,3,4,5,7,8,9,10,11,12]


def array(value):
    if isinstance(value,torch.Tensor):
        value=value.detach().cpu()
        if value.is_floating_point(): value=value.float()
        return value.numpy().copy()
    return np.array(value,copy=True)


def safe_json(value):
    if isinstance(value,dict): return {str(k):safe_json(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [safe_json(v) for v in value]
    if isinstance(value,np.ndarray): return safe_json(value.tolist())
    if isinstance(value,np.generic): return safe_json(value.item())
    if isinstance(value,Path): return str(value)
    if isinstance(value,float) and not np.isfinite(value): return str(value)
    return value


def opening(chunk,prefix):
    return {label:{side:dict(max_mm=float(values[:,dim].max()*1000),
        **{'first_gt_'+str(t)+'mm':int(found[0]) if len(found:=np.flatnonzero(values[:,dim]*1000>t)) else None
           for t in (1,10,30)}) for side,dim in (('left',6),('right',13))}
        for label,values in (('prefix',chunk[:prefix]),('full',chunk))}


class ActRefillCapture:
    def __init__(self,policy,preprocessor,postprocessor,features,action_keys,folder,*,execute,
                 fps=30.,context=None,model_dir=None):
        self.policy=policy; self.pre=preprocessor; self.post=copy.deepcopy(postprocessor)
        self.features=features; self.keys=list(action_keys); self.folder=Path(folder)
        self.execute=execute; self.fps=fps; self.context=copy.deepcopy(context or {})
        self.context.update(chunk_size=policy.config.chunk_size,n_action_steps=policy.config.n_action_steps,
            temporal_ensemble_coeff=policy.config.temporal_ensemble_coeff)
        self.context['numeric_context']=dict(torch_version=torch.__version__,default_dtype=str(torch.get_default_dtype()),
            float32_matmul_precision=torch.get_float32_matmul_precision(),policy_training=getattr(policy,'training',None),
            use_amp=policy.config.use_amp,cudnn_benchmark=torch.backends.cudnn.benchmark,
            cudnn_deterministic=torch.backends.cudnn.deterministic,cudnn_allow_tf32=torch.backends.cudnn.allow_tf32,
            cuda_matmul_allow_tf32=torch.backends.cuda.matmul.allow_tf32)
        root=Path(__file__).resolve().parents[1]
        files=[Path(__file__),root/'deployment_entry/cobot_hybrid.py',root/'configs/cobot_motion.json',
            root/'runtime_source/src/lerobot/policies/act/modeling_act.py',
            root/'runtime_source/src/lerobot/rollout/inference/sync.py']
        self.context['source_files']={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in files if path.is_file()}
        self.context['saved_normalizer_stats']=[{k:array(v).tolist() for k,v in s.state_dict().items()}
            for s in self.pre.steps if type(s).__name__=='NormalizerProcessorStep']
        self.context['saved_normalizer_configs']=[s.get_config() for s in self.pre.steps if type(s).__name__=='NormalizerProcessorStep']
        self.constants=[item for s in self.pre.steps if type(s).__name__=='TrainingConstantEffortStep' for item in s.description()]
        self.context['effort_adaptation']=dict(constant_efforts=self.constants)
        self.model_dir=Path(model_dir) if model_dir is not None else None
        self.context['model_files']=self._model_hashes()
        self.blocks=[]; self.current=None; self.step=None; self.errors=[]; self.overhead={}
        self.installed=False; self._old={}; self._original_select=None; self._original_predict=None

    def _model_hashes(self):
        return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.model_dir.iterdir()
                if p.suffix in ('.json','.safetensors')} if self.model_dir is not None else {}

    def _error(self,stage,error):
        self.errors.append(dict(stage=stage,step=self.step,error=f'{type(error).__name__}: {error}'))

    def _observe(self,stage,callback):
        started=time.perf_counter()
        try: callback()
        except Exception as error: self._error(stage,error)
        finally:
            entry=self.overhead.setdefault(stage,dict(calls=0,cpu_call_s=0.,max_cpu_call_s=0.))
            duration=time.perf_counter()-started
            entry['calls']+=1; entry['cpu_call_s']+=duration; entry['max_cpu_call_s']=max(entry['max_cpu_call_s'],duration)

    @staticmethod
    def _clone(value):
        return value.detach().clone() if isinstance(value,torch.Tensor) else copy.deepcopy(value)

    def install(self):
        if self.installed: raise ValueError('Recorder already installed')
        self._original_select=self.policy.select_action; self._original_predict=self.policy.predict_action_chunk
        self._old={name:(name in self.policy.__dict__,self.policy.__dict__.get(name)) for name in ('select_action','predict_action_chunk')}
        self.policy.select_action=self._select; self.policy.predict_action_chunk=self._predict
        self.installed=True

    def uninstall(self):
        if self.installed:
            for name,(present,value) in self._old.items():
                if present: setattr(self.policy,name,value)
                else: delattr(self.policy,name)
            self.installed=False

    def before_get_action(self,step,refill,frame,*,tick_s,previous,cameras=None):
        self.step=step
        if not refill: return
        block=dict(start_step=step,directory=f'refill_{len(self.blocks):04d}_step{step:06d}',
            tick_monotonic_s=tick_s,frame_receipt_monotonic_s=time.perf_counter(),previous=array(previous),
            forward_calls=0,input_batches=0,raw_frame={},model_inputs={},non_tensor_inputs={},
            normalized_chunk=None,selected_norm=[],selected_physical=[],dispatches=[],camera_cache={})
        self.blocks.append(block); self.current=block
        def snapshot():
            block['raw_frame']={k:array(v) for k,v in frame.items() if k=='observation.state' or k.startswith('observation.images.')}
            block['camera_cache']={role:dict(latest_timestamp=getattr(camera,'latest_timestamp',None),
                sampled_perf_s=time.perf_counter(),basis='cache metadata after RGB retrieval; not atomic or hardware exposure')
                for role,camera in (cameras or {}).items()}
        self._observe('frame_snapshot',snapshot)

    def _copy_model_inputs(self,batch):
        block=self.current; block['input_batches']+=1
        block['model_inputs']={k:self._clone(v) for k,v in batch.items() if isinstance(v,torch.Tensor)}
        block['non_tensor_inputs']={k:copy.deepcopy(v) for k,v in batch.items() if not isinstance(v,torch.Tensor)}
        block['model_input_perf_s']=time.perf_counter()

    def _select(self,batch):
        if self.current is not None and self.current['start_step']==self.step and not self.current['model_inputs']:
            self._observe('model_input_snapshot',lambda:self._copy_model_inputs(batch))
        value=self._original_select(batch)
        if self.current is not None:
            self._observe('selected_norm_snapshot',lambda:self.current['selected_norm'].append(
                dict(step=self.step,value=self._clone(value))))
        return value

    def _predict(self,batch,*args,**kwargs):
        block=self.current
        if block is not None: block['forward_calls']+=1; block['predict_started_perf_s']=time.perf_counter()
        try: value=self._original_predict(batch,*args,**kwargs)
        except BaseException as error:
            self._error('original_model_exception',error)
            raise
        if block is not None:
            def snapshot():
                block['normalized_chunk']=self._clone(value)
                block['predict_returned_perf_s']=time.perf_counter()
            self._observe('chunk_snapshot',snapshot)
        return value

    def after_get_action(self,step,value):
        if self.current is not None:
            self._observe('selected_physical_snapshot',lambda:self.current['selected_physical'].append(
                dict(step=step,value=array(value).reshape(-1))))

    def after_dispatch(self,step,act_action,target,measured,previous,*,tick_s,returned_s,grippers,dispatched):
        if self.current is not None:
            self._observe('dispatch_snapshot',lambda:self.current['dispatches'].append(dict(
                step=step,source_offset=step-self.current['start_step'],tick_monotonic_s=tick_s,
                send_action_returned_monotonic_s=returned_s,send_action_returned=bool(dispatched),
                act_action=array(act_action),target=array(target),measured_before_action=array(measured),
                previous=array(previous),grippers=copy.deepcopy(grippers))))

    @staticmethod
    def _stack(entries):
        return np.stack([array(v['value']).reshape(14) for v in entries]) if entries else np.empty((0,14),dtype=np.float32)

    def _save_block(self,block):
        import cv2
        path=self.folder/block['directory']; path.mkdir(parents=True,exist_ok=True)
        raw=block['raw_frame']; data={}; images={}; inputs={}
        if 'observation.state' in raw:
            data['observation_state_raw']=raw['observation.state']
            effective=raw['observation.state'].copy()
            for item in self.constants: effective[...,item['index']]=item['training_constant']
            data['observation_state_after_effort_adapter']=effective
        for role in ROLES:
            key='observation.images.'+role
            if key not in raw: continue
            pixels=raw[key]; data['rgb_'+role]=pixels
            if not cv2.imwrite(str(path/(role+'.png')),cv2.cvtColor(pixels,cv2.COLOR_RGB2BGR)):
                raise RuntimeError('Failed saving RGB '+role)
            images[role]=dict(path=role+'.png',array='rgb_'+role,shape=list(pixels.shape),color_order='RGB',
                pixel_sha256=hashlib.sha256(pixels.tobytes()).hexdigest())
        for index,(key,value) in enumerate(block['model_inputs'].items()):
            name=f'model_input_{index:02d}'; data[name]=array(value)
            inputs[key]=dict(array=name,shape=list(value.shape),tensor_dtype=str(value.dtype),device=str(value.device))
            if key=='observation.state': data['normalized_state_actual']=data[name].copy()
        selected_norm=self._stack(block['selected_norm']); selected=self._stack(block['selected_physical'])
        data['selected_prefix_normalized']=selected_norm; data['selected_prefix_unclipped']=selected
        if len(selected_norm): data['selected_action_normalized']=selected_norm[:1]
        if len(selected): data['act_first_unclipped']=selected[0]
        full=None; checks={}; openings=None
        if block['normalized_chunk'] is not None:
            data['act_chunk_normalized']=array(block['normalized_chunk'])
            with torch.inference_mode(): physical=copy.deepcopy(self.post)(block['normalized_chunk'].clone())
            order=[self.features['action']['names'].index(k) for k in self.keys]
            full=array(physical)[:,:,order]; data['act_chunk_unclipped']=full
            block['exported_physical_chunk']=full[0]
            prefix=self.policy.config.n_action_steps
            data['candidate_prefix_unclipped']=full[0,:prefix].copy()
            openings=opening(full[0],prefix)
            checks['selected_norm_vs_chunk_prefix_max_error']=float(np.max(abs(
                selected_norm-data['act_chunk_normalized'][0,:len(selected_norm)]))) if len(selected_norm) else None
            checks['selected_physical_vs_chunk_prefix_max_error']=float(np.max(abs(selected-full[0,:len(selected)]))) if len(selected) else None
        sent=[row for row in block['dispatches'] if row['send_action_returned']]
        data['sent_targets']=np.stack([row['target'] for row in sent]) if sent else np.empty((0,14))
        data['sent_source_offsets']=np.asarray([row['source_offset'] for row in sent],dtype=np.int64)
        data['measured_before_sent_action']=np.stack([row['measured_before_action'] for row in sent]) if sent else np.empty((0,14))
        prefix=self.policy.config.n_action_steps
        complete=(block['forward_calls']==1 and block['input_batches']==1 and len(images)==3 and
            'normalized_state_actual' in data and full is not None and bool(len(selected)) and
            checks.get('selected_norm_vs_chunk_prefix_max_error',float('inf')) is not None and
            checks.get('selected_physical_vs_chunk_prefix_max_error',float('inf')) is not None and
            checks['selected_norm_vs_chunk_prefix_max_error']<=1e-6 and checks['selected_physical_vs_chunk_prefix_max_error']<=1e-6)
        report=dict(schema='act-live-refill-v1',complete=complete,execute=self.execute,
            controller_commands_sent=bool(sent),targets_dispatched=bool(sent),start_step=block['start_step'],
            observation_names=self.features['observation.state']['names'],action_names=self.keys,
            model_action_names=self.features['action']['names'],context=self.context,
            images=images,model_inputs=inputs,model_non_tensor_inputs=block['non_tensor_inputs'],
            forward_calls=block['forward_calls'],select_calls=len(selected_norm),
            timestamp_basis='local perf_counter; raw camera frames not hardware synchronized',
            observation_receipts={k:block.get(k) for k in ('tick_monotonic_s','frame_receipt_monotonic_s','model_input_perf_s',
                'predict_started_perf_s','predict_returned_perf_s')},camera_cache=block['camera_cache'],
            candidate_prefix_count=prefix,selected_count=len(selected),dispatch_returned_count=len(sent),
            sent_prefix_complete=self.execute and len(sent)==prefix,physical_execution_verified=False,checks=checks,opening=openings,
            execution_note='send_action returned, NOT CAN wire capture or controller ACK; feedback is measured before each action',
            arrays_file='arrays.npz')
        np.savez_compressed(path/'arrays.npz',**data)
        report['arrays_sha256']=hashlib.sha256((path/'arrays.npz').read_bytes()).hexdigest()
        (path/'prefix_execution.json').write_text(json.dumps(safe_json(block['dispatches']),indent=2,allow_nan=False),encoding='utf-8')
        (path/'report.json').write_text(json.dumps(safe_json(report),indent=2,allow_nan=False),encoding='utf-8')
        return dict(directory=block['directory'],start_step=block['start_step'],complete=complete,
            selected_count=len(selected),dispatch_returned_count=len(sent),sent_prefix_complete=report['sent_prefix_complete'],
            opening=openings,checks=checks,arrays_sha256=report['arrays_sha256'])

    def _seams(self):
        result=[]
        for previous,current in zip(self.blocks,self.blocks[1:]):
            old_sent=[r for r in previous['dispatches'] if r['send_action_returned']]
            new_sent=[r for r in current['dispatches'] if r['send_action_returned']]
            row=dict(previous_start_step=previous['start_step'],next_start_step=current['start_step'],
                previous_prefix_complete=len(old_sent)==self.policy.config.n_action_steps,
                observation_interval_s=current['frame_receipt_monotonic_s']-previous['frame_receipt_monotonic_s'])
            if old_sent and new_sent:
                row['last_sent_vs_next_first_joint_max_rad']=float(np.max(abs(new_sent[0]['target'][ARM]-old_sent[-1]['target'][ARM])))
                row['dispatch_interval_s']=new_sent[0]['send_action_returned_monotonic_s']-old_sent[-1]['send_action_returned_monotonic_s']
            old=previous.get('exported_physical_chunk'); new=current.get('exported_physical_chunk')
            prefix=self.policy.config.n_action_steps
            if old is not None and new is not None and row['previous_prefix_complete']:
                count=min(len(old)-prefix,prefix,len(new))
                if count>0:
                    difference=old[prefix:prefix+count]-new[:count]
                    row['nominal_tail_vs_next_prefix']=dict(count=count,
                        joint_MAE_rad=float(abs(difference[:,ARM]).mean()),joint_max_rad=float(abs(difference[:,ARM]).max()),
                        gripper_max_mm=float(abs(difference[:,[6,13]]).max()*1000),
                        basis='Nominal action-index overlap only; excludes observed pause/jitter and is not hardware trajectory continuity')
            result.append(row)
        return result

    def finish(self,*,run_context=None):
        """Restore methods and persist AFTER original hold/disconnect, even on failure."""
        self.uninstall()
        result=dict(schema='act-live-refill-session-v1',execute=self.execute,capture_complete=False,
            context=self.context,run_context=run_context or {},chunks=[],errors=self.errors,seams=[],
            overhead=self.overhead,io_basis='PNG/NPZ/JSON written only after original exit cleanup',
            timing_note='Observer CPU call durations exclude asynchronous GPU work; instrumented timing is not uninstrumented timing',
            execution_note='Captured successful application send calls, not independent controller acceptance or completed physical motion')
        try:
            print(f'Saving {len(self.blocks)} ACT refill snapshots after original exit cleanup...',flush=True)
            self.folder.mkdir(parents=True,exist_ok=True)
            for block in self.blocks:
                try: result['chunks'].append(self._save_block(block))
                except Exception as error:
                    self._error('save_block',error)
                    result['chunks'].append(dict(directory=block['directory'],start_step=block['start_step'],
                        complete=False,selected_count=len(block['selected_physical']),
                        dispatch_returned_count=sum(r['send_action_returned'] for r in block['dispatches']),sent_prefix_complete=False))
            result['seams']=self._seams()
            result['total_dispatch_returned']=sum(c['dispatch_returned_count'] for c in result['chunks'])
            result['model_files_unchanged_after']=self._model_hashes()==self.context['model_files']
            result['capture_complete']=bool(self.blocks) and not self.errors and result['model_files_unchanged_after'] and all(c['complete'] for c in result['chunks'])
            (self.folder/'summary.json').write_text(json.dumps(safe_json(result),indent=2,allow_nan=False),encoding='utf-8')
        except Exception as error:
            self._error('save_session',error)
            result['capture_complete']=False
            print('WARNING: refill capture save failed after original cleanup: '+str(error),flush=True)
        print('ACT_REFILL_CAPTURE='+json.dumps(dict(summary=str((self.folder/'summary.json').resolve()),
            capture_complete=result['capture_complete'],chunks=len(self.blocks),errors=len(self.errors),
            total_dispatch_returned=result.get('total_dispatch_returned',0))),flush=True)
        return result
