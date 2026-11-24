#!/usr/bin/env python3
"""Read-only local readiness inventory. Never enables, resets or sends robot actions."""
import argparse
import datetime
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
BUNDLE=ROOT/'amd_hybrid_deploy_20261031'
POLICY=BUNDLE/'piper_towel_replica'
RECORDS=ROOT.parent/'local_environment_record'


def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--software-only',action='store_true',help='Require local software checks; still report disconnected devices')
    parser.add_argument('--probe-fpga',action='store_true',help='Read-only lifecycle status and diagnostic PL probe; no start/finish or robot commands')
    parser.add_argument('--out',type=Path,default=ROOT/'npu_deploy/artifacts/readiness/latest.json')
    args=parser.parse_args()
    report={'checked_at':datetime.datetime.now().astimezone().isoformat(),
            'robot_commands_sent':False,'resource_envelope':{k:os.environ.get(k) for k in ['AIPC_RUNTIME_CPU_QUOTA','AIPC_RUNTIME_MEMORY_MAX','AIPC_RUNTIME_THREADS']},'software_checks':{},'hardware_checks':{},'notes':[]}
    checks=report['software_checks']
    def record(name,passed,detail):checks[name]={'passed':bool(passed),'detail':detail}
    def run(command):
        try:
            p=subprocess.run(command,text=True,capture_output=True,timeout=30)
            return {'returncode':p.returncode,'stdout':p.stdout[-10000:],'stderr':p.stderr[-2000:]}
        except (OSError,subprocess.TimeoutExpired) as exc:return {'returncode':-1,'error':str(exc)}
    record('python',sys.version_info[:2]==(3,12),{'version':sys.version,'executable':sys.executable})
    for name in ['torch','torchvision','transformers','num2words','cv2','can','serial','safetensors','piper_sdk']:
        record('module_'+name,importlib.util.find_spec(name) is not None,'importable module')
    required=[POLICY/'models/act050000/model.safetensors',POLICY/'models/smol030000/model.safetensors',
              BUNDLE/'assets/smolvlm2_500m/model.safetensors',POLICY/'deployment_assets/task_start.json',
              POLICY/'configs/cobot_observation.json',POLICY/'configs/cobot_motion.json',
              ROOT/'npu_deploy/artifacts/smol_vision/smol_vision.onnx',ROOT/'npu_deploy/artifacts/smol_vision/export.json',
              ROOT/'ryzen-ai-npu/venv/bin/python',Path('/opt/xilinx/xrt/setup.sh')]
    record('runtime_files',all(p.is_file() for p in required),{'missing':[str(p) for p in required if not p.is_file()]})
    cache=POLICY/'deployment_assets/torch/hub/checkpoints/resnet18-f37072fd.pth'
    record('act_backbone_cache',cache.is_file() and digest(cache)=='f37072fd47e89c5e827621c5baffa7500819f7896bbacec160b1a16c560e07ec',str(cache))
    site=json.loads((POLICY/'configs/cobot_site.json').read_text())
    record('model_selection',site.get('models')=={'act':'models/act050000','smolvla':'models/smol030000'},site.get('models'))
    budget=run([sys.executable,str(ROOT/'npu_deploy/field_budget.py')])
    record('field_budget',budget['returncode']==0,budget)
    soft,hard=resource.getrlimit(resource.RLIMIT_MEMLOCK)
    record('npu_memlock',soft==resource.RLIM_INFINITY or soft>=1073741824,{'soft_bytes':soft,'hard_bytes':hard})
    try:
        import torch
        available=bool(torch.version.hip) and torch.cuda.is_available()
        if available:
            x=torch.eye(16,device='cuda'); y=x@x;torch.cuda.synchronize()
            available=bool(torch.isfinite(y).all())
        record('rocm_device',available,{'torch':torch.__version__,'hip':torch.version.hip,'device':torch.cuda.get_device_name(0) if available else None})
    except Exception as exc:record('rocm_device',False,str(exc))
    for backend in ['cpu','rocm']:
        p=RECORDS/f'models-deployment-{backend}.json'
        try:
            r=json.loads(p.read_text())
            valid=r.get('passed') is True and r.get('backend')==backend
            for label,folder in [('act','act050000'),('smolvla','smol030000')]:
                row=r.get('policies',{}).get(label,{})
                valid=valid and row.get('passed') is True and row.get('strict_loading') is True
                model=POLICY/'models'/folder
                valid=valid and row.get('weights_sha256')==digest(model/'model.safetensors') and row.get('config_sha256')==digest(model/'config.json')
            record('model_inference_'+backend,valid,str(p))
        except (OSError,ValueError) as exc:record('model_inference_'+backend,False,str(exc))
    try:
        r=json.loads((ROOT/'npu_deploy/artifacts/npu_preflight/latest_success.json').read_text())
        art=ROOT/'npu_deploy/artifacts/smol_vision'
        valid=(r.get('passed') is True and r['onnx_sha256']==digest(art/'smol_vision.onnx')
               and r['fixture_sha256']==digest(art/'fixture.npz'))
        record('npu_encoder_execution',valid,r)
    except (OSError,ValueError,KeyError) as exc:record('npu_encoder_execution',False,str(exc))
    try:
        path=ROOT/'npu_deploy/artifacts/smol_vision/hybrid_offline.json'
        r=json.loads(path.read_text())
        valid=(r.get('steps_requested',0)>=20 and r.get('matched_steps',0)>=20 and r.get('stability_repeats',0)>=100
               and r.get('native',{}).get('error') is None and r.get('npu',{}).get('error') is None
               and r.get('fixture_note')=='SYNTHETIC')
        record('hybrid_synthetic_replay',valid,{'report':str(path),'source_decisions_differ':r.get('source_decisions_differ'),
               'max_target_abs_difference_per_dim':r.get('max_target_abs_difference_per_dim')})
    except (OSError,ValueError) as exc:record('hybrid_synthetic_replay',False,str(exc))
    power=run(['powerprofilesctl','get'])
    record('cpu_performance_mode',power.get('stdout','').strip()=='performance',power)
    with tempfile.TemporaryDirectory(prefix='aipc_ready_') as temporary:
        path=Path(temporary)/'platform.json'
        result=run(['/opt/xilinx/xrt/bin/xrt-smi','examine','--report','platform','--format','JSON','--output',str(path)])
        try:
            platforms=[p for dev in json.loads(path.read_text())['devices'] for p in dev.get('platforms',[])]
            modes=[p.get('status',{}).get('power_mode') for p in platforms]
            record('npu_performance_mode',bool(modes) and all(mode in ('performance','turbo') for mode in modes),modes)
        except (OSError,ValueError,KeyError) as exc:record('npu_performance_mode',False,str(exc))
    for name in ['ip','candump','v4l2-ctl','lsusb','ping','systemd-run']:
        record('tool_'+name,shutil.which(name) is not None,shutil.which(name))
    profile_path=ROOT/'npu_deploy/fpga_board/active_profile.json'
    fpga_profile=json.loads(profile_path.read_text()) if profile_path.is_file() else {'mode':'b32'}
    fpga_mode=fpga_profile.get('mode','b32')
    key=ROOT/'npu_deploy/artifacts/fpga_lifecycle/control.key'
    if fpga_mode=='fpg1_v3':
        sys.path.insert(0,str(ROOT/'npu_deploy/fpga_board'))
        try:
            from jupyter_client import JupyterBoard
            record('fpga_management_session',JupyterBoard().authenticated(),'Authenticated Jupyter session; no board key migration')
        except Exception as exc:record('fpga_management_session',False,str(exc))
    else:
        record('fpga_local_pairing_material',key.is_file(),'Legacy B32 key presence only; remote pairing not inferred')
    hardware=report['hardware_checks']
    video=sorted(str(p) for p in Path('/dev').glob('video*'))
    hardware['camera_nodes']={'passed':len(video)>=3,'nodes':video}
    ports=sorted(p.name for p in Path('/sys/class/net').iterdir() if (p/'type').read_text().strip()=='280')
    hardware['can_interfaces']={'passed':len(ports)>=2,'ports':ports}
    physical=run(['bash',str(BUNDLE/'run.sh'),'prepare','--check-only'])
    hardware['saved_device_identity']={'passed':physical['returncode']==0,'result':physical}
    if args.probe_fpga:
        sys.path.insert(0,str(ROOT/'npu_deploy'))
        try:
            if fpga_mode=='fpg1_v3':
                sys.path.insert(0,str(ROOT/'npu_deploy/fpga_board'))
                sys.path.insert(0,str(ROOT/'fpga_integration/fpga_code/fpga_workspace'))
                from control_jupyter import request as jupyter_control
                from host.fold_client import FoldClient
                from host.protocol import Sample,UNKNOWN
                from host.reference import load_profile,profile_crc
                state=jupyter_control('status')
                if state['state']!='running':raise RuntimeError('Selected FPGA service is not running')
                profile=load_profile();start=tuple(profile['start']);zeros=(0,)*14
                client=FoldClient(('192.168.2.99',9009),profile_crc(profile),timeout=.5)
                try:
                    sample=Sample(profile_crc(profile),UNKNOWN,0,0,UNKNOWN,UNKNOWN,start,start,start,start,zeros,zeros)
                    response=client.probe(sample)
                    result={'build_id':hex(response.values[8]),'profile_crc':hex(response.values[0]),
                            'phase_state':response.values[5],'verify_state':(response.values[6]>>8)&3,
                            'session':response.session,'probe_is_not_task':True}
                finally:client.close()
                hardware['fpga_board_pairing']={'passed':True,'state':state,'pl_probe':result}
                if fpga_profile.get('phase_config'):
                    from host.fold_config import load as load_fold_config, identity as fold_identity
                    from jupyter_client import JupyterBoard
                    local_identity=fold_identity(load_fold_config())
                    remote_prefix=fpga_profile['board_root'].split('/jupyter_notebooks/',1)[1]
                    board=JupyterBoard()
                    config_text=board.request('GET','/api/contents/'+remote_prefix+'/config/fold_verify_profile.json',params={'format':'text'}).json()['content']
                    remote_identity=fold_identity(json.loads(config_text))
                    lcd_text=board.request('GET','/api/contents/'+remote_prefix+'/runs/lcd_latest.json',params={'format':'text'}).json()['content']
                    lcd_state=json.loads(lcd_text)
                    matched=(local_identity==remote_identity==fpga_profile['phase_config'] and lcd_state.get('phase_config_crc')==local_identity['crc32'])
                    hardware['fpga_configuration_identity']={'passed':matched,'local':local_identity,'board':remote_identity,'pl_startup_checked_crc':lcd_state.get('phase_config_crc')}
                    from maintenance_status import inspect as inspect_maintenance
                    hardware.update(inspect_maintenance(board,fpga_profile['board_root'],result['build_id']))

            else:
                from fpga_lifecycle.control_client import request
                from fpga_live_shadow import from_environment
                address=os.environ.get('AIPC_FPGA_SHADOW','192.168.2.99:9009')
                state=request('status',host=address.rsplit(':',1)[0]);os.environ['AIPC_FPGA_SHADOW']=address
                shadow=from_environment(max_actions=750,handoff_step=300)
                try:result=shadow.probe()
                finally:shadow.close()
                hardware['fpga_board_pairing']={'passed':True,'state':state,'pl_probe':result}

        except Exception as exc:hardware['fpga_board_pairing']={'passed':False,'detail':str(exc)}
    else:
        hardware['fpga_board_pairing']={'passed':False,'detail':'Use --probe-fpga for an actual diagnostic check; no start/finish is sent'}
    report['fpga_profile']=fpga_profile
    report['field_validation_ready']=False

    report['notes']=['Synthetic model/PL checks validate execution paths, not field accuracy or task success.',
                     'Current active FPGA mode: '+fpga_mode+'. Explicit B32 rollback is separate; no automatic downgrade.',
                     'B35 revision bit/HWH selected; B34 rollback preserved. PYNQ reference is calibrated from the 33.333333MHz device tree.',
                     'B35 expiry/frame-gap/snapshot/phase-CRC revisions are accepted with SYNTHETIC evidence. Field vision remains disabled until calibration.']
    report['software_ready']=all(row['passed'] for row in checks.values())
    report['hardware_ready']=all(row['passed'] for row in hardware.values())
    report['ready_for_motion']=report['software_ready'] and report['hardware_ready'] and report['field_validation_ready']
    report['fold_success_certified']=False
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('LOCAL_SOFTWARE_READY='+str(report['software_ready']).lower())
    print('HARDWARE_READY='+str(report['hardware_ready']).lower())
    print('READY_FOR_MOTION='+str(report['ready_for_motion']).lower())
    for group in ['software_checks','hardware_checks']:
        for name,row in report[group].items():
            if not row['passed']:print('PENDING='+group+'.'+name)
    print('READINESS_REPORT='+str(args.out))
    return 0 if report['software_ready'] and (args.software_only or report['hardware_ready']) else 2

if __name__=='__main__':raise SystemExit(main())
