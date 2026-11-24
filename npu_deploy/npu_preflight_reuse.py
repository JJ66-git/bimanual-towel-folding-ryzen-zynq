"""Reuse real NPU preflight evidence while its model, runtime and boot remain valid."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import time

ROOT=Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as file:return hashlib.file_digest(file,'sha256').hexdigest()


def runtime_fingerprint(root=ROOT):
    paths=[Path('/opt/xilinx/xrt/version.json'),Path('/opt/xilinx/xrt/lib/libxrt_core.so'),
           Path('/sys/module/amdxdna/version'),Path('/sys/module/amdxdna/srcversion')]
    paths+=list((root/'ryzen-ai-npu/venv/lib').glob('python*/site-packages/*onnxruntime*.dist-info/METADATA'))
    paths+=list((root/'ryzen-ai-npu/venv/lib').glob('python*/site-packages/voe*.dist-info/METADATA'))
    files={}
    for path in paths:
        if path.is_file():
            stat=path.stat()
            files[str(path)]=dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns,
                content=path.read_text().strip() if str(path).startswith('/sys/') else None)
    return dict(kernel=platform.release(),files=files)


def reusable(root=ROOT,*,now=None,uptime=None,fingerprint=None):
    now=time.time() if now is None else now
    uptime=float(Path('/proc/uptime').read_text().split()[0]) if uptime is None else uptime
    try:
        report=json.loads((root/'npu_deploy/artifacts/npu_preflight/latest_success.json').read_text())
        checked=datetime.datetime.fromisoformat(report['checked_at']).timestamp()
        if not now-uptime <= checked <= now+5:return False,'预检不属于本次开机'
        if not (report.get('passed') and report.get('npu_partition_confirmed') and report.get('valid_image_outputs')==3):
            return False,'缺少真实 NPU 分区和三路有效输出证据'
        actual=runtime_fingerprint(root) if fingerprint is None else fingerprint
        if 'runtime_fingerprint' in report:
            if report['runtime_fingerprint']!=actual:return False,'NPU 运行时或驱动已变化'
        elif any(row['mtime_ns']/1e9>checked for row in actual['files'].values()):
            return False,'旧预检之后 NPU 运行时已更新'
        art=root/'npu_deploy/artifacts/smol_vision'
        for name,key in [('smol_vision.onnx','onnx_sha256'),('fixture.npz','fixture_sha256')]:
            if digest(art/name)!=report[key]:return False,name+' 已变化'
        log=Path(report['session'])/'npu.log'
        if digest(log)!=report['log_sha256']:return False,'预检日志身份不匹配'
        return True,report['checked_at']
    except (OSError,ValueError,KeyError,TypeError) as error:return False,str(error)


def main():
    valid,detail=reusable()
    if os.environ.get('AIPC_NPU_FORCE_CHECK')=='1':valid,detail=False,'已选择重新检查'
    print(('NPU_PREFLIGHT_REUSED: ' if valid else 'NPU_PREFLIGHT_REQUIRED: ')+detail,flush=True)
    return 0 if valid else 2


if __name__=='__main__':raise SystemExit(main())
