"""Relocatable launcher for the packaged native controller. No motion unless --execute."""
import argparse, json, os, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase', choices=['reset','act','hybrid'])
    p.add_argument('--backend',choices=['cpu','rocm'],default='cpu')
    p.add_argument('--precision',choices=['auto','original','float32'],default='auto')
    p.add_argument('--execute',action='store_true')
    p.add_argument('--steps',type=int)
    p.add_argument('--check-only',action='store_true',help='Print arguments; no model/device access')
    p.add_argument('--preview',action='store_true')
    p.add_argument('--trace',action='store_true')
    p.add_argument('--capture-refills',action='store_true')
    args=p.parse_args()
    if args.steps is not None and (args.steps<1 or args.phase=='reset'): p.error('steps requires act/hybrid and positive integer')
    if args.phase=='reset' and (args.preview or args.capture_refills): p.error('reset has no preview/refill capture')
    site=json.loads((ROOT/'configs/cobot_site.json').read_text())
    common=['--left-can',site['robot']['left_port'],'--right-can',site['robot']['right_port']]
    pose=ROOT/site['training_start_pose']
    target=json.loads(pose.read_text())['target']
    import math
    if len(target)!=14 or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in target):
        raise ValueError('Invalid own task start')
    steps=args.steps or site['max_actions']
    if args.phase=='hybrid' and steps<=site['handoff_step']: p.error('hybrid steps must exceed handoff_step; use act for shorter baseline')
    if args.phase=='reset':
        entry=ROOT/'deployment_entry/cobot_reset.py'
        launch=[sys.executable,str(entry),*common,'--arm','both','--target',*map(str,target)]
    else:
        entry=ROOT/'deployment_entry/cobot_hybrid.py'
        launch=[sys.executable,str(entry),*common,'--backend',args.backend,'--precision',args.precision,
          '--act-model',str(ROOT/site['models']['act']),'--smol-model',str(ROOT/site['models']['smolvla']),
          '--motion-profile',str(ROOT/'configs/cobot_motion.json'),'--start-pose-file',str(pose),
          '--fps',str(site['control_fps']),'--task',site['task'],'--max-actions',str(steps),
          '--handoff-step',str(site['handoff_step'] if args.phase=='hybrid' else 0)]
        for role in ('left','middle','right'): launch += ['--'+role+'-index',str(site['camera_indices'][role])]
        if args.phase=='act': launch.append('--act-only')
    if args.execute: launch.append('--execute')
    directory=ROOT/'logs'/f'{args.phase}_{time.time_ns()}'
    if args.capture_refills:
        launch += ['--capture-refills',str(directory/'refills'),'--trace-file',str(directory/'trace.json')]
    elif args.trace and args.phase!='reset': launch += ['--trace-file',str(directory/'trace.json')]
    if args.trace and args.phase=='reset':
        launch=[sys.executable,str(ROOT/'scripts/reset_start_trace.py'),'--out-dir',str(directory),'--',*launch[2:]]
    if args.phase!='reset':
        launch=[sys.executable,str(ROOT/'scripts/task_preview_entry.py'),*([] if args.preview else ['--no-preview']),
                '--entry',launch[1],'--',*launch[2:]]
    if args.check_only:
        print(json.dumps(launch,ensure_ascii=False)); return
    if sys.version_info[:2]!=(3,12) or sys.platform!='linux': raise RuntimeError('Requires Linux Python 3.12')
    from prepare_cobot import usb_serial
    for side in ('left','right'):
        port=site['robot'][side+'_port']
        if usb_serial((Path('/sys/class/net')/port/'device').resolve(strict=True))!=site['usb_serials'][side]:
            raise ValueError('CAN USB identity mismatch; run ./run.sh prepare --check-only')
    os.chdir(ROOT)
    print('PORTABLE_COBOT:',args.phase,'EXECUTE' if args.execute else 'READ-ONLY',args.backend,flush=True)
    os.execv(sys.executable,launch)

if __name__=='__main__': main()
