#!/usr/bin/env python3
"""Real fixed-image ACT/SmolVLA inference load; no camera or actuator imports."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time

PROJECT=Path(__file__).resolve().parents[2];BUNDLE=PROJECT/'amd_hybrid_deploy_20261031';POLICY=BUNDLE/'piper_towel_replica'
sys.path[:0]=[str(POLICY/'runtime_source/src'),str(POLICY/'deployment_entry'),str(PROJECT/'npu_deploy')]
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TORCH_HOME=str(POLICY/'deployment_assets/torch'))


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp');temporary.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');temporary.replace(path)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--backend',choices=['cpu','rocm'],default='cpu')
    p.add_argument('--vision-worker',choices=['native','cpu','npu'],default='native');p.add_argument('--threads',type=int,default=4)
    p.add_argument('--duration-s',type=float,default=60);p.add_argument('--ready-file',type=Path,required=True)
    p.add_argument('--stop-file',type=Path);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--fixture',type=Path,default=BUNDLE/'fixtures/observation.json');args=p.parse_args()
    if args.duration_s<0 or args.threads<1:p.error('Invalid duration or thread count')
    import numpy as np
    import torch
    from PIL import Image
    from portable_backend import select_device,seed_all,smol_config,place_policy
    from lerobot.policies.act.configuration_act import ACTConfig
    from lerobot.policies.act.modeling_act import ACTPolicy
    from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy
    from lerobot.policies.factory import make_pre_post_processors
    from lerobot.policies.utils import prepare_observation_for_inference
    from lerobot.policies.rtc.configuration_rtc import RTCConfig
    from lerobot.configs import RTCAttentionSchedule
    from training_image_adapter import install_training_image_adapter
    from training_effort_adapter import STATE_NAMES,install_training_effort_adapter
    from cobot_motion_adapter import load_motion_profile,configure_act
    import blend_core
    torch.set_num_threads(args.threads);device=select_device(args.backend);seed_all(42,device)
    stopping=False
    def stop(*_):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    fixture_path=args.fixture.resolve();fixture=json.loads(fixture_path.read_text())
    values=dict(zip(fixture['feature_names'],fixture['state'],strict=True))
    if set(values)!=set(STATE_NAMES):raise ValueError('Fixture state contract mismatch')
    frame={'observation.state':np.asarray([values[name] for name in STATE_NAMES],dtype=np.float32)}
    for role,name in fixture['images'].items():frame['observation.images.'+role]=np.asarray(Image.open(fixture_path.parent/name).convert('RGB'))
    def processors(policy,checkpoint,label):
        pre,post=make_pre_post_processors(policy.config,str(checkpoint),preprocessor_overrides={'device_processor':{'device':str(device)}})
        install_training_image_adapter(pre,POLICY/'configs/cobot_observation.json',checkpoint,label)
        install_training_effort_adapter(pre,STATE_NAMES,label);return pre,post
    act_path=POLICY/'models/act050000';smol_path=POLICY/'models/smol030000'
    motion=load_motion_profile(POLICY/'configs/cobot_motion.json',blend_core)
    config=configure_act(ACTConfig.from_pretrained(str(act_path)),motion);config.device=str(device)
    act=ACTPolicy.from_pretrained(str(act_path),config=config,strict=True);place_policy(act,device,'auto');act_pre,act_post=processors(act,act_path,'act')
    smol=SmolVLAPolicy.from_pretrained(str(smol_path),config=smol_config(smol_path,device),strict=True)
    place_policy(smol,device,'auto');smol_pre,smol_post=processors(smol,smol_path,'smolvla')
    worker=None;original_embed=smol.model.vlm_with_expert.embed_image;rows=[];grad_calls=0;initial_grad=torch.autograd.grad
    def counted_grad(*a,**kw):
        nonlocal grad_calls
        grad_calls+=1;return initial_grad(*a,**kw)
    torch.autograd.grad=counted_grad
    def infer(policy,pre,post,expected,**kwargs):
        seed_all(42,device);policy.reset()
        if device.type=='cuda':torch.cuda.synchronize()
        start=time.perf_counter_ns()
        batch=pre(prepare_observation_for_inference(copy.deepcopy(frame),device,fixture['task'],'piper_dual'))
        normalized=policy.predict_action_chunk(batch,**kwargs);output=post(normalized)
        if device.type=='cuda':torch.cuda.synchronize()
        end=time.perf_counter_ns()
        if tuple(output.shape)!=expected or not torch.isfinite(output).all():raise RuntimeError('Invalid policy output')
        return normalized.detach().clone(),dict(started_ns=start,returned_ns=end,elapsed_ms=(end-start)/1e6,shape=list(output.shape),finite=True)
    report=dict(schema='fpga_fixed_image_model_load_v1',passed=False,backend=args.backend,vision_worker=args.vision_worker,
        gpu_execution=device.type=='cuda',npu_execution=False,real_camera_input=False,full_controller_load_measured=False,
        fixture=str(fixture_path),fixture_source=fixture.get('source_kind'),robot_commands_sent=False,
        boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),pid=os.getpid(),rows=rows)
    try:
        if args.vision_worker!='native':
            from vision_client import VisionClient
            dtype=next(smol.model.vlm_with_expert.get_vlm_model().vision_model.parameters()).dtype
            worker=VisionClient(args.vision_worker,PROJECT/'npu_deploy/artifacts/smol_vision/smol_vision.onnx',dtype)
            smol.model.vlm_with_expert.embed_image=worker
        prefix,plain=infer(smol,smol_pre,smol_post,(1,50,14));report['plain_warmup']=plain
        smol.config.rtc_config=RTCConfig(enabled=True,mode='guided',prefix_attention_schedule=RTCAttentionSchedule.EXP,max_guidance_weight=2.5,execution_horizon=10,debug=False)
        smol.init_rtc_processor();load_started=time.perf_counter();index=0
        while not stopping and (index==0 or time.perf_counter()-load_started<args.duration_s) and not (args.stop_file and args.stop_file.exists()):
            _,act_timing=infer(act,act_pre,act_post,(1,100,14))
            _,smol_timing=infer(smol,smol_pre,smol_post,(1,50,14),prev_chunk_left_over=prefix[:,4:].clone(),inference_delay=4,execution_horizon=10)
            rows.append(dict(iteration=index,act=act_timing,smol_guided=smol_timing));index+=1
            report['npu_execution']=bool(worker and args.vision_worker=='npu' and worker._first_result_reported and worker.log_monitor.partition_seen.is_set())
            if args.vision_worker=='npu' and not report['npu_execution']:raise RuntimeError('NPU execution evidence absent')
            if grad_calls==0:raise RuntimeError('Guided autograd was not exercised')
            report.update(passed=True,guided_autograd_calls=grad_calls,ready_monotonic_ns=time.perf_counter_ns())
            atomic_json(args.ready_file,{k:v for k,v in report.items() if k!='rows'})
            atomic_json(args.out,report);print('MODEL_LOAD_ITERATION='+str(index),flush=True)
        if not rows:raise RuntimeError('Stopped before a verified load iteration')
        report['ended_monotonic_ns']=time.perf_counter_ns();report['guided_autograd_calls']=grad_calls
    except BaseException as error:
        report.update(passed=False,error=type(error).__name__+': '+str(error));raise
    finally:
        torch.autograd.grad=initial_grad;smol.model.vlm_with_expert.embed_image=original_embed
        if worker:worker.close()
        atomic_json(args.out,report)
    return 0


if __name__=='__main__':raise SystemExit(main())
