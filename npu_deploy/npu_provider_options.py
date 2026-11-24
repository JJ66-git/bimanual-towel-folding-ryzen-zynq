"""Shared persistent VitisAI cache, separated by model/runtime/driver identity.

Provider option names follow AMD Ryzen AI 1.9 modelrun documentation:
https://ryzenai.docs.amd.com/en/latest/modelrun.html#vitisai-ep-cache
"""
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def provider_options(model,ort_version):
    identity={'schema':1,'model_sha256':digest(model),'onnxruntime':ort_version,'kernel':platform.release()}
    for name in ['onnxruntime-vitisai','voe','flexml']:
        try:identity[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:identity[name]=None
    for name in ['version','srcversion']:
        path=Path('/sys/module/amdxdna')/name
        identity['amdxdna_'+name]=path.read_text().strip() if path.is_file() else None
    for name in ['vendor','device','fw_version']:
        path=Path('/sys/class/accel/accel0/device')/name
        identity['npu_'+name]=path.read_text().strip() if path.is_file() else None
    for name in ['libxrt_coreutil.so.2','libxrt_driver_xdna.so.2']:
        path=Path('/opt/xilinx/xrt/lib')/name
        identity[name]=digest(path) if path.is_file() else None
    key=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    cache=ROOT/'npu_deploy/artifacts/npu_cache'
    cache.mkdir(parents=True,exist_ok=True)
    (cache/(key+'.identity.json')).write_text(json.dumps(identity,indent=2)+'\n')
    return {'cache_dir':str(cache),'cache_key':key,'enable_cache_file_io_in_mem':'0'}


def enable_execution_profile(options):
    """Record one real warm-up to confirm cached sessions execute VitisAI kernels."""
    import os
    import time
    directory=ROOT/'npu_deploy/artifacts/npu_profiles'
    directory.mkdir(parents=True,exist_ok=True)
    options.enable_profiling=True
    options.profile_file_prefix=str(directory/f'proof_{os.getpid()}_{time.time_ns()}')


def confirm_execution(session):
    import numpy as np
    import sys
    session.run(['image_features'],{'pixel_values':np.zeros((1,3,512,512),dtype=np.float32)})
    path=Path(session.end_profiling())
    events=json.loads(path.read_text())
    count=sum(event.get('cat')=='Node' and event.get('args',{}).get('provider')=='VitisAIExecutionProvider'
              and event.get('dur',0)>0 for event in events)
    if not count:raise RuntimeError('No executed VitisAIExecutionProvider kernel in warm-up profile; CPU fallback is not accepted')
    print(f'NPU_EXECUTION_CONFIRMED provider=VitisAIExecutionProvider kernels={count} profile={path}',file=sys.stderr,flush=True)
    return {'executed_npu_kernels':count,'profile':str(path)}
