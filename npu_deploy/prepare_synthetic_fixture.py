#!/usr/bin/env python3
"""Create clearly marked synthetic inputs and native PyTorch encoder references, without devices."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
BUNDLE=ROOT/'amd_hybrid_deploy_20261031'
POLICY=BUNDLE/'piper_towel_replica'
sys.path[:0]=[str(POLICY/'runtime_source/src'),str(POLICY/'deployment_entry')]
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TORCH_HOME=str(POLICY/'deployment_assets/torch'))
import numpy as np
import torch
from PIL import Image
from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy
from lerobot.policies.factory import make_pre_post_processors
from lerobot.policies.utils import prepare_observation_for_inference
from lerobot.processor.normalize_processor import NormalizerProcessorStep
from portable_backend import smol_config,place_policy
from training_effort_adapter import STATE_NAMES,install_training_effort_adapter
from training_image_adapter import install_training_image_adapter


def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'npu_deploy/artifacts/synthetic_preflight')
    args=parser.parse_args()
    out=args.out.resolve()
    # Never overwrite handed-over reference data or a previous generated fixture.
    if (out/'fixture.npz').exists() or (out/'observation.json').exists():
        parser.error('Output already has fixture data; choose a fresh --out directory')
    torch.set_num_threads(4)
    out.mkdir(parents=True,exist_ok=True)
    checkpoint=POLICY/'models/smol030000'
    device=torch.device('cpu')
    policy=SmolVLAPolicy.from_pretrained(str(checkpoint),config=smol_config(checkpoint,device),strict=True,local_files_only=True)
    place_policy(policy,device,'float32')
    pre,_=make_pre_post_processors(policy.config,str(checkpoint),preprocessor_overrides={'device_processor':{'device':'cpu'}})
    install_training_image_adapter(pre,POLICY/'configs/cobot_observation.json',checkpoint,'smolvla')
    install_training_effort_adapter(pre,STATE_NAMES,'smolvla')
    normalizer=next(step for step in pre.steps if isinstance(step,NormalizerProcessorStep))
    state=normalizer.state_dict()['observation.state.mean'].detach().float().cpu().numpy().reshape(28)
    observation={'source_kind':'SYNTHETIC','qualification':'Generated gradient/color images and saved mean state; no field capture, accuracy or fold-success evidence.',
                 'feature_names':STATE_NAMES,'state':state.tolist(),'task':'Fold the towel with both Piper arms.','images':{}}
    frame={'observation.state':state.copy()}
    y,x=np.indices((480,640))
    for index,role in enumerate(['left','middle','right']):
        pixels=np.stack([(x+index*37)%256,(y+index*61)%256,((x//2+y//2)+index*83)%256],axis=-1).astype(np.uint8)
        name=f'synthetic_{role}.png'
        Image.fromarray(pixels).save(out/name)
        observation['images'][role]=name
        frame['observation.images.'+role]=pixels
    batch=pre(prepare_observation_for_inference(copy.deepcopy(frame),device,observation['task'],'piper_dual'))
    images,masks=policy.prepare_images(batch)
    assert len(images)==3 and all(bool(mask.all()) for mask in masks)
    with torch.inference_mode():
        features=[policy.model.vlm_with_expert.embed_image(image).detach().float().cpu().numpy() for image in images]
    assert all(x.shape==(1,64,960) and np.isfinite(x).all() for x in features)
    np.savez_compressed(out/'fixture.npz',**{f'image_{i}':image.cpu().numpy() for i,image in enumerate(images)},
                        **{f'feature_{i}':feature for i,feature in enumerate(features)})
    (out/'observation.json').write_text(json.dumps(observation,ensure_ascii=False,indent=2)+'\n')
    identity={'source_kind':'SYNTHETIC','reference_backend':'native SmolVLA PyTorch CPU float32 embed_image; independent of ONNX',
              'weights_sha256':digest(checkpoint/'model.safetensors'),'config_sha256':digest(checkpoint/'config.json'),
              'image_contract_sha256':digest(POLICY/'configs/cobot_observation.json'),
              'fixture_sha256':digest(out/'fixture.npz'),'torch':torch.__version__,
              'qualification':observation['qualification']}
    (out/'fixture_metadata.json').write_text(json.dumps(identity,ensure_ascii=False,indent=2)+'\n')
    # Reuse exact handed-over ONNX without modifying it.
    onnx=ROOT/'npu_deploy/artifacts/smol_vision/smol_vision.onnx'
    target=out/'smol_vision.onnx'
    if not target.exists():target.symlink_to(os.path.relpath(onnx,out))
    print('SYNTHETIC_FIXTURE_READY='+str(out),flush=True)

if __name__=='__main__':main()
