"""Offline ACT + Smol plain/guided validation using actual packaged checkpoints; no robot imports."""
import argparse, copy, json, os, platform, resource, sys, time
from pathlib import Path

B=Path(__file__).resolve().parents[1]
P=B/'piper_towel_replica'
sys.path[:0]=[str(P/'runtime_source/src'),str(P/'deployment_entry')]
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TORCH_HOME=str(P/'deployment_assets/torch'))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend',choices=['cpu','rocm'],default='cpu')
    p.add_argument('--precision',choices=['auto','original','float32'],default='auto')
    p.add_argument('--warmup',type=int,default=1)
    p.add_argument('--repeat',type=int,default=5)
    p.add_argument('--threads',type=int,default=4)
    p.add_argument('--out',type=Path,default=B/'validation/benchmark.json')
    args=p.parse_args()
    if min(args.repeat,args.threads)<1 or args.warmup<0: p.error('repeat/threads positive, warmup nonnegative')
    import numpy as np
    import torch
    from PIL import Image
    from portable_backend import select_device, seed_all, smol_config, place_policy
    from lerobot.policies.act.configuration_act import ACTConfig
    from lerobot.policies.act.modeling_act import ACTPolicy
    from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy
    from lerobot.policies.factory import make_pre_post_processors
    from lerobot.policies.utils import prepare_observation_for_inference
    from lerobot.policies.rtc.configuration_rtc import RTCConfig
    from lerobot.configs import RTCAttentionSchedule
    from training_image_adapter import install_training_image_adapter
    from training_effort_adapter import STATE_NAMES, install_training_effort_adapter
    from cobot_motion_adapter import load_motion_profile, configure_act
    import blend_core

    torch.set_num_threads(args.threads)
    device=select_device(args.backend)
    fixture=json.loads((B/'fixtures/observation.json').read_text())
    # Recorded raw dictionary order differs from model feature order. Reorder by exact feature name.
    values=dict(zip(fixture['feature_names'],fixture['state'],strict=True))
    if set(values)!=set(STATE_NAMES): raise ValueError('Fixture state keys differ from native model contract')
    frame={'observation.state':np.asarray([values[name] for name in STATE_NAMES],dtype=np.float32)}
    for role,filename in fixture['images'].items():
        frame['observation.images.'+role]=np.array(Image.open(B/'fixtures'/filename).convert('RGB'))
    report={'backend':args.backend,'precision':args.precision,'torch':torch.__version__,'hip':torch.version.hip,
      'host':platform.platform(),'python':sys.version,'threads':args.threads,'warmup':args.warmup,'repeat':args.repeat,
      'state_order':STATE_NAMES,'fixture_note':fixture['source_kind'],'hardware_access':False,'policies':{}}
    def sync():
        if device.type=='cuda': torch.cuda.synchronize()
    def processors(policy,path,label):
        pre,post=make_pre_post_processors(policy.config,str(path),preprocessor_overrides={'device_processor':{'device':str(device)}})
        install_training_image_adapter(pre,P/'configs/cobot_observation.json',path,label)
        install_training_effort_adapter(pre,STATE_NAMES,label)
        return pre,post
    def measure(label,policy,pre,post,expected,kwargs=None):
        times=[]; output=None
        for index in range(args.warmup+args.repeat):
            seed_all(42,device); policy.reset()
            sync(); started=time.perf_counter()
            batch=pre(prepare_observation_for_inference(copy.deepcopy(frame),device,fixture['task'],'piper_dual'))
            normalized=policy.predict_action_chunk(batch,**(kwargs or {}))
            output=post(normalized)
            sync(); elapsed=time.perf_counter()-started
            if index>=args.warmup: times.append(elapsed)
            if tuple(output.shape)!=expected or not bool(torch.isfinite(output).all()):
                raise RuntimeError(f'{label}: invalid output {tuple(output.shape)}')
            print(f'{label} sample {index+1}: {elapsed:.3f}s shape={tuple(output.shape)} finite',flush=True)
        report['policies'][label]={'seconds':times,'p50_s':float(np.percentile(times,50)),
          'p95_s':float(np.percentile(times,95)),'max_s':max(times),'shape':list(output.shape),
          'finite':True,'first_action':output[0,0].detach().float().cpu().tolist(),
          'min':float(output.min()),'max':float(output.max())}
        return normalized.detach().clone(),output.detach().float().cpu().numpy()

    seed_all(42,device)
    started=time.perf_counter()
    motion=load_motion_profile(P/'configs/cobot_motion.json',blend_core)
    cfg=configure_act(ACTConfig.from_pretrained(str(P/'models/act050000')),motion); cfg.device=str(device)
    act=ACTPolicy.from_pretrained(str(P/'models/act050000'),config=cfg,strict=True)
    place_policy(act,device,args.precision)
    act_pre,act_post=processors(act,P/'models/act050000','act')
    report['act_load_s']=time.perf_counter()-started
    _,act_array=measure('act',act,act_pre,act_post,(1,100,14))
    started=time.perf_counter()
    smol=SmolVLAPolicy.from_pretrained(str(P/'models/smol030000'),config=smol_config(P/'models/smol030000',device),strict=True)
    place_policy(smol,device,args.precision)
    smol_pre,smol_post=processors(smol,P/'models/smol030000','smolvla')
    report['smol_load_s']=time.perf_counter()-started
    prefix,plain=measure('smol_plain',smol,smol_pre,smol_post,(1,50,14))
    smol.config.rtc_config=RTCConfig(enabled=True,mode='guided',prefix_attention_schedule=RTCAttentionSchedule.EXP,
                                    max_guidance_weight=2.5,execution_horizon=10,debug=False)
    smol.init_rtc_processor()
    grad_calls=0; original_grad=torch.autograd.grad
    def counted_grad(*a,**k):
        nonlocal grad_calls
        grad_calls+=1
        return original_grad(*a,**k)
    torch.autograd.grad=counted_grad
    try:
        _,guided=measure('smol_guided',smol,smol_pre,smol_post,(1,50,14),
                         {'prev_chunk_left_over':prefix[:,4:].clone(),'inference_delay':4,'execution_horizon':10})
    finally: torch.autograd.grad=original_grad
    if grad_calls==0: raise RuntimeError('Guided autograd branch was not exercised')
    report['guided_autograd_calls']=grad_calls
    report['max_rss_mib']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
    if device.type=='cuda': report['device_peak_allocated_mib']=torch.cuda.max_memory_allocated()/2**20
    report['passed']=True
    report['qualification']='Offline fixed-input shape/finite/autograd smoke and timing only. Not full temporal controller, ROS/CAN, robot motion, numerical equivalence or task success.'
    args.out.parent.mkdir(parents=True,exist_ok=True)
    np.savez_compressed(args.out.with_suffix('.npz'),act=act_array,smol_plain=plain,smol_guided=guided)
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    print('BENCHMARK_REPORT='+str(args.out),flush=True)

if __name__=='__main__': main()
