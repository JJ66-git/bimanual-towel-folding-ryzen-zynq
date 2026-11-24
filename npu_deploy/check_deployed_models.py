#!/usr/bin/env python3
"""Validate deployed policy weights/processors with synthetic inputs; no hardware control."""
import argparse
import copy
import datetime
import gc
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
B=ROOT/'amd_hybrid_deploy_20261031'
P=B/'piper_towel_replica'
sys.path[:0]=[str(P/'runtime_source/src'),str(P/'deployment_entry')]
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TORCH_HOME=str(P/'deployment_assets/torch'))
for name in ['OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS']:
    os.environ.setdefault(name,'4')
import numpy as np
import torch
from lerobot.policies.act.configuration_act import ACTConfig
from lerobot.policies.act.modeling_act import ACTPolicy
from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy
from lerobot.policies.factory import make_pre_post_processors
from lerobot.policies.utils import prepare_observation_for_inference
from portable_backend import smol_config,place_policy,seed_all,select_device
from training_image_adapter import install_training_image_adapter
from training_effort_adapter import STATE_NAMES,install_training_effort_adapter
from lerobot.processor.normalize_processor import NormalizerProcessorStep

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend',choices=['cpu','rocm'],default='cpu')
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--precision',choices=['auto','float32'],default='auto')
    args=parser.parse_args()
    torch.set_num_threads(4)
    device=select_device(args.backend)
    report={'checked_at':datetime.datetime.now().astimezone().isoformat(),'backend':args.backend,
            'torch':torch.__version__,'hip':torch.version.hip,'precision':args.precision,
            'input_source':'SYNTHETIC gradient RGB images; saved mean state; no real camera observation',
            'scope':'strict weight loading, saved processors/adapters, finite action chunk; not accuracy or closed-loop validation',
            'robot_control':False,'policies':{}}
    try:
        for label,cls,folder in [('act',ACTPolicy,'act050000'),('smolvla',SmolVLAPolicy,'smol030000')]:
            path=P/'models'/folder
            cfg=ACTConfig.from_pretrained(str(path)) if label=='act' else smol_config(path,device)
            cfg.device=str(device)
            start=time.perf_counter()
            print('LOADING',label,str(path),flush=True)
            policy=cls.from_pretrained(str(path),config=cfg,strict=True,local_files_only=True)
            place_policy(policy,device,args.precision)
            pre,post=make_pre_post_processors(policy.config,str(path),preprocessor_overrides={'device_processor':{'device':str(device)}})
            install_training_image_adapter(pre,P/'configs/cobot_observation.json',path,label)
            install_training_effort_adapter(pre,STATE_NAMES,label)
            normalizer=next(x for x in pre.steps if isinstance(x,NormalizerProcessorStep))
            state=normalizer.state_dict()['observation.state.mean'].detach().float().cpu().numpy().reshape(28)
            pixels=np.empty((480,640,3),dtype=np.uint8)
            pixels[:,:,0]=np.arange(640,dtype=np.uint16)[None,:]%256
            pixels[:,:,1]=np.arange(480,dtype=np.uint16)[:,None]%256
            pixels[:,:,2]=96
            frame={'observation.state':state.copy()}
            for name in ['left','middle','right']:frame['observation.images.'+name]=pixels.copy()
            batch=pre(prepare_observation_for_inference(copy.deepcopy(frame),device,'Fold the towel.','piper_dual'))
            seed_all(42,device)
            policy.reset()
            with torch.inference_mode():
                chunk=policy.predict_action_chunk(batch)
                action=post(chunk)
            if device.type=='cuda':torch.cuda.synchronize()
            assert torch.isfinite(chunk).all() and torch.isfinite(action).all(),'non-finite outputs'
            assert tuple(action.shape)==(1,cfg.chunk_size,14),action.shape
            report['policies'][label]={'passed':True,'strict_loading':True,'model':str(path),
                    'resolved_model':str(path.resolve()),
                    'weights_sha256':hashlib.file_digest((path/'model.safetensors').open('rb'),'sha256').hexdigest(),
                    'config_sha256':hashlib.sha256((path/'config.json').read_bytes()).hexdigest(),'action_shape':list(action.shape),
                    'max_abs_action':float(action.abs().max()),'elapsed_s':round(time.perf_counter()-start,3)}
            print('MODEL_PASSED',json.dumps(report['policies'][label]),flush=True)
            del policy,pre,post,normalizer,chunk,action,batch
            gc.collect()
            if device.type=='cuda':torch.cuda.empty_cache()
        report['passed']=True
    except Exception as exc:
        report['passed']=False
        report['error']=f'{type(exc).__name__}: {exc}'
        raise
    finally:
        args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('DEPLOYED_MODELS_PASSED',args.backend,flush=True)

if __name__=='__main__':main()
