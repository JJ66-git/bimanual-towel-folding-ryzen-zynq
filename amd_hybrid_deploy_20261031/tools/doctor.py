"""Host inventory; no CAN frames, robot creation, camera streams or configuration writes."""
import argparse, json, os, platform, shutil, subprocess, sys
from pathlib import Path

def query(args):
    if not shutil.which(args[0]): return {'available':False}
    try:
        r=subprocess.run(args, text=True, capture_output=True, timeout=20)
        return {'returncode':r.returncode,'stdout':r.stdout[-14000:],'stderr':r.stderr[-2000:]}
    except Exception as error: return {'error':str(error)}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend',choices=['cpu','rocm'],default='cpu')
    p.add_argument('--out',type=Path)
    args=p.parse_args()
    report={'python':sys.version,'executable':sys.executable,'platform':platform.platform(),
            'machine':platform.machine(),'backend_requested':args.backend,
            'os_release':Path('/etc/os-release').read_text() if Path('/etc/os-release').exists() else None,
            'lscpu':query(['lscpu']),'pci':query(['lspci','-nn']),
            'memory':query(['free','-h']),'groups':query(['id']),
            'rocm':query(['rocminfo']),'can':query(['ip','-details','link','show','type','can']),
            'device_nodes':{str(p):os.access(p,os.R_OK|os.W_OK) for pattern in ('/dev/kfd','/dev/dri/render*','/dev/accel/*') for p in Path('/').glob(pattern.lstrip('/'))}}
    valid=False
    try:
        import torch, torchvision
        report['torch']={'version':torch.__version__,'torchvision':torchvision.__version__,
                         'hip':torch.version.hip,'cuda':torch.version.cuda,'available':torch.cuda.is_available()}
        valid=(sys.version_info[:2]==(3,12) and sys.platform=='linux' and platform.machine()=='x86_64')
        if args.backend=='rocm':
            valid=valid and bool(torch.version.hip) and torch.cuda.is_available()
            if valid:
                report['torch']['device']=torch.cuda.get_device_name(0)
                report['torch']['gcn_arch']=getattr(torch.cuda.get_device_properties(0),'gcnArchName',None)
                x=torch.randn(128,128,device='cuda'); y=x@x
                torch.cuda.synchronize()
                report['torch']['matmul_finite']=bool(torch.isfinite(y).all())
                valid=valid and report['torch']['matmul_finite']
        else:
            x=torch.randn(128,128); valid=valid and bool(torch.isfinite(x@x).all())
    except Exception as error: report['torch_error']=repr(error)
    report['basic_backend_check_passed']=valid
    report['qualification']='Inventory and basic tensor check only; not model/latency/robot validation. NPU is not used.'
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if valid else 1

if __name__=='__main__': raise SystemExit(main())
