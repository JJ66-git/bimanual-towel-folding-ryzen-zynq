"""Record a local task and optionally preview its existing camera caches."""
import argparse
import datetime
import json
import hashlib
from pathlib import Path
import runpy
import shutil
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent))
from shared_runtime.camera_preview import TaskCameraPreview
from real_run_recording import RunRecorder, Tee


def snapshot(directory, entry, arguments):
    files = [*ROOT.joinpath('deployment_entry').glob('*.py'),
             *ROOT.joinpath('configs').glob('cobot_*.json'),
             ROOT/'deployment_assets/task_start.json', Path(__file__),
             ROOT/'scripts/real_run_recording.py', ROOT/'scripts/run_cobot.py',
             ROOT/'scripts/towel_quality.py',
             ROOT.parent/'shared_runtime/camera_preview.py',
             ROOT.parent/'shared_runtime/environment.json']
    for flag in ('--act-model', '--smol-model'):
        if flag in arguments:
            model = Path(arguments[arguments.index(flag)+1])
            files.extend(p for p in model.iterdir() if p.is_file() and
                         (p.suffix == '.json' or ('processor' in p.name and p.suffix == '.safetensors')))
    records = []
    for source in dict.fromkeys(files):
        if not source.is_file():
            continue
        try:
            relative = source.relative_to(ROOT.parent)
        except ValueError:
            relative = Path('external')/source.name
        target = directory/'runtime_snapshot'/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        records.append(dict(source=str(source), saved_as=str(target.relative_to(directory)),
                            sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
    metadata = dict(entry=str(entry), arguments=arguments, python=sys.executable,
                    start_wall_time_s=time.time(), start_monotonic_s=time.perf_counter(),
                    files=records, weights_copied=False,
                    scope='Run-time snapshot before robot construction; original policy/control code retained')
    (directory/'launch.json').write_text(json.dumps(metadata, indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--entry',type=Path,required=True)
    parser.add_argument('--no-preview',action='store_true',help='Record without opening a preview window')
    parser.add_argument('arguments',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    entry=args.entry.resolve(strict=True)
    arguments=args.arguments[1:] if args.arguments[:1]==['--'] else args.arguments
    from lerobot.robots.piper_dual.piper_dual import PIPERDual

    directory=ROOT/'logs'/(datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_task_recording')
    directory.mkdir(parents=True)
    original_arguments=arguments.copy()
    if not any(a=='--trace-file' or a.startswith('--trace-file=') for a in arguments):
        arguments += ['--trace-file', str(directory/'policy_trace.json')]
    snapshot(directory, entry, arguments)
    recorder=RunRecorder(directory)
    guide=None
    if '--quality-profile' in arguments:
        from towel_quality import TowelQualityMonitor
        monitor=TowelQualityMonitor(arguments[arguments.index('--quality-profile')+1])
        guide=monitor.preview_guide
    preview=(None if args.no_preview else
             TaskCameraPreview(title='双臂任务 · 实时相机与状态',transform=guide))
    original_connect=PIPERDual.connect
    original_observe=PIPERDual.get_observation
    original_send=PIPERDual.send_action
    old_argv=sys.argv;old_path=sys.path.copy();error=None
    old_stdout,old_stderr=sys.stdout,sys.stderr
    console=(directory/'console.log').open('w',buffering=1)
    lock=threading.Lock()
    sys.stdout=Tee(old_stdout,console,lock);sys.stderr=Tee(old_stderr,console,lock)

    def connected(robot,*values,**kwargs):
        result=original_connect(robot,*values,**kwargs)
        try:recorder.attach(robot.cameras)
        except Exception as problem:print('TASK_RECORD_ATTACH_ERROR='+str(problem),file=sys.stderr,flush=True)
        try:
            if preview is not None:preview.attach(robot.cameras, robot=robot)
        except Exception as problem:print('CAMERA_PREVIEW_ATTACH_ERROR='+str(problem),file=sys.stderr,flush=True)
        return result

    def observed(robot,*values,**kwargs):
        result=original_observe(robot,*values,**kwargs)
        try:
            if preview is not None:preview.update_observation(result)
        except Exception as problem:
            preview.stats['sample_errors']+=1
            preview.stats['last_error']=type(problem).__name__+': '+str(problem)
        try:
            names=[k for k in result if k.endswith(('.pos','.effort'))]
            recorder.record_event('observation',dict(feature_names=names,
                state=[float(result[k]) for k in names]))
        except Exception as problem:recorder._increment('sample_errors',problem)
        return result

    def sent(robot,action,*values,**kwargs):
        before=time.perf_counter()
        try:requested={k:float(v) for k,v in action.items()}
        except Exception:requested={}
        result=original_send(robot,action,*values,**kwargs)
        try:recorder.record_event('action',dict(requested=requested,
            send_started_monotonic_s=before,send_returned_monotonic_s=time.perf_counter()))
        except Exception as problem:recorder._increment('sample_errors',problem)
        return result

    try:
        if preview is not None:preview.start()  # GUI failure remains before robot construction.
        PIPERDual.connect=connected
        PIPERDual.get_observation=observed
        PIPERDual.send_action=sent
        sys.argv=[str(entry),*arguments];sys.path.insert(0,str(entry.parent))
        print('OWN_RUN_RECORDING='+str(directory),flush=True)
        runpy.run_path(str(entry),run_name='__main__')
    except BaseException as problem:
        if not isinstance(problem,SystemExit) or problem.code not in (None,0):error=type(problem).__name__+': '+str(problem)
        raise
    finally:
        PIPERDual.connect=original_connect
        PIPERDual.get_observation=original_observe
        PIPERDual.send_action=original_send
        sys.argv=old_argv;sys.path[:]=old_path
        try:
            for name,close in [('recorder',recorder.close),
                               ('preview',preview.close if preview is not None else lambda:None)]:
                try:close()
                except Exception as problem:print('TASK_RECORD_CLEANUP_ERROR '+name+': '+str(problem),file=sys.stderr,flush=True)
            try:
                (directory/'preview.json').write_text(json.dumps(dict(entry=str(entry),
                    original_arguments=original_arguments,arguments=arguments,error=error,
                    preview=preview.stats if preview is not None else None,
                    recording=recorder.stats,additional_camera_opens=0,additional_motor_commands=0,
                    scope='Observational recording only; task outcome is not inferred from exit status'),indent=2))
            except OSError as problem:print('TASK_RECORD_METADATA_ERROR='+str(problem),file=sys.stderr,flush=True)
        finally:
            sys.stdout=old_stdout;sys.stderr=old_stderr
            console.close()


if __name__=='__main__':main()
