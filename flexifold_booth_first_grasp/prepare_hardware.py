"""Install the paired B35 display once, verify real PL replies, then open the booth.

No robot imports or CAN commands. Versioned display/protocol files are backed up
before replacement. Bitstream and model files are untouched.
"""
import argparse
import base64
import fcntl
import hashlib
import json
import os
import socket
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parent
MANAGEMENT=PROJECT/'npu_deploy/fpga_board'
RUNTIME=PROJECT/'fpga_integration/fpga_code/fpga_workspace'
FILES=('board/service_control.py','board/fpg1_v3_server.py','board/booth_display.py','board/fold_display.py',
       'board/fold_bridge.py','board/isolated_display.py',
       'screen/lcd.py','screen/cooperative_state.py','screen/cooperative_view.py','host/task_status.py','host/action_status.py',
       'screen/assets/DisplaySansSC.otf','screen/assets/DisplaySansSC-Bold.otf')


def checked_execute(board,code):
    result=board.execute(code,timeout=40)
    if not result['passed']:raise RuntimeError(str(result['errors']))
    return result['output']


def capture_board_rollback(board,profile,directory):
    import importlib.util
    path=ROOT.parent/'tools/snapshot_board.py'
    spec=importlib.util.spec_from_file_location('board_rollback_capture',path)
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    result=helper.snapshot(board,profile,directory)
    if result.get('passed') is not True or result.get('board_root')!=profile['board_root']:
        raise RuntimeError('Rollback snapshot did not verify the selected board runtime')
    return Path(directory)/'board_snapshot.json'


def deploy(board,profile,control):
    remote=profile['board_root']
    if not remote.startswith('/home/xilinx/jupyter_notebooks/fpga_fpg1_v3_b35_') or '..' in Path(remote).parts:
        raise ValueError('Expected explicitly selected versioned B35 runtime')
    hashes={name:hashlib.sha256((RUNTIME/name).read_bytes()).hexdigest() for name in FILES}
    code="""from pathlib import Path
import hashlib,json
root=Path(%r)
names=%r
print('BOOTH_FILE_HASHES='+json.dumps({name:hashlib.sha256((root/name).read_bytes()).hexdigest() if (root/name).is_file() else None for name in names}))
"""%(remote,FILES)
    output=checked_execute(board,code)
    actual=json.loads(next(s.split('=',1)[1] for s in output.splitlines() if s.startswith('BOOTH_FILE_HASHES=')))
    changed=[name for name in FILES if hashes[name]!=actual[name]]
    if not changed:
        print('BOOTH_BOARD_FILES_REUSED: B35 display files already match',flush=True)
        return
    print('BOOTH_BOARD_UPDATE: '+', '.join(changed),flush=True)
    # Capture actual board files/bitstreams before stopping or replacing anything.
    snapshot_path=ROOT.parent/'runtime_snapshots/board'/str(time.time_ns())
    manifest=capture_board_rollback(board,profile,snapshot_path)
    print('BOARD_ROLLBACK_SNAPSHOT='+str(manifest),flush=True)
    control('stop')
    backup='.booth_backups/'+str(time.time_ns())
    checked_execute(board,"""from pathlib import Path
import shutil
root=Path(%r)
for name in %r:
 source=root/name
 if source.is_file():
  target=root/%r/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
"""%(remote,changed,backup))
    try:
        prefix=str(Path(remote).relative_to('/home/xilinx/jupyter_notebooks'))
        for name in changed:board.upload(prefix+'/'+name,(RUNTIME/name).read_bytes())
        checked_execute(board,"""from pathlib import Path
import hashlib
root=Path(%r)
for name,digest in %r.items():
 data=(root/name).read_bytes()
 assert hashlib.sha256(data).hexdigest()==digest,name
 if name.endswith('.py'):compile(data,str(root/name),'exec')
print('BOOTH_BOARD_FILES_VERIFIED')
"""%(remote,hashes))
    except BaseException:
        checked_execute(board,"""from pathlib import Path
import shutil
root=Path(%r)
for name in %r:
 saved=root/%r/name
 if saved.is_file():shutil.copy2(saved,root/name)
 elif (root/name).exists():(root/name).unlink()
"""%(remote,changed,backup))
        try:control('start')
        except Exception as restore_error:print('BOOTH_ROLLBACK_START_FAILED='+str(restore_error),flush=True)
        raise


def collect_diagnostics(board,profile):
    code="""from pathlib import Path
import json,sys
import PIL
root=Path(%r)
try:state=json.loads((root/'runs/service_state.json').read_text())
except (OSError,ValueError):state={}
console=Path(state.get('console',str(root/'runs/missing.log')))
text=console.read_text(errors='replace')[-12000:] if str(console).startswith(str(root)+'/runs/') and console.is_file() else ''
journal=Path(state.get('log',str(root/'runs/missing.jsonl')))
tail=''
if str(journal).startswith(str(root)+'/runs/') and journal.is_file():
 with journal.open('rb') as stream:
  stream.seek(max(0,journal.stat().st_size-65536));tail=stream.read().decode('utf-8',errors='replace')
print('BOOTH_DIAGNOSTICS='+json.dumps({'service_state':state,'console_tail':text,'journal_tail':tail,'python':sys.version,'pillow':PIL.__version__}))
"""%profile['board_root']
    output=checked_execute(board,code)
    return json.loads(next(line.split('=',1)[1] for line in output.splitlines() if line.startswith('BOOTH_DIAGNOSTICS=')))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only',action='store_true')
    parser.add_argument('--check-only',action='store_true')
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--leader-can',default=os.environ.get('AIPC_LEADER_CAN'),help='operator-confirmed isolated leader interface; never inferred from numbering')
    parser.add_argument('--auto-leader-can',action='store_true',help='detect an isolated leader interface on request')
    args=parser.parse_args()
    if not 1<=args.port<=65535:parser.error('invalid local HTTP port')
    if args.check_only:
        print('B35 interactive: reuse/install paired display -> ensure managed recorded_replay service -> real 2.650/2.540/2.550/2.540 checks -> compare LCD state -> browser. No CAN or robot motion.')
        return 0
    sys.path.insert(0,str(PROJECT/'npu_deploy'))
    from operation_lock_path import operation_lock_path
    lock_path=operation_lock_path(PROJECT)
    lock_path.parent.mkdir(parents=True,exist_ok=True)
    lock=lock_path.open('a+b')
    try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise RuntimeError('任务或另一互动入口仍在运行，请先退出该流程。')
    os.set_inheritable(lock.fileno(),True)
    if not args.verify_only:
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as check:
            try:check.bind(('127.0.0.1',args.port))
            except OSError as error:raise RuntimeError('互动服务端口已占用，请结束上一互动流程后重试。') from error
    sys.path.insert(0,str(MANAGEMENT))
    from jupyter_client import JupyterBoard
    from control_jupyter import request,PROFILE
    profile=json.loads(PROFILE.read_text())
    if profile['expected_build_id']!='0x46504735':raise RuntimeError('Interactive package requires the selected B35 release')
    board=JupyterBoard()
    # A transport failure must not be presented as a rejected password.
    board.request('GET','/login')
    if not board.authenticated():board.login_dialog()
    folder=ROOT/'verification'/str(time.time_ns());folder.mkdir(parents=True)
    report_path=folder/'b35_check.json'
    deploy(board,profile,request)
    print('BOOTH_SERVICE='+json.dumps(request('ensure',source='recorded_replay')),flush=True)
    print('等待板端完成启动与显示初始化……',flush=True)
    try:
        sys.path.insert(0,str(PROJECT/'npu_deploy'))
        from fpga_service_probe import probe
        probe(profile['address'])
        result=subprocess.run([sys.executable,str(ROOT/'verify_fpga.py'),'--out',str(report_path)],cwd=ROOT)
        if result.returncode:raise RuntimeError(json.loads(report_path.read_text()).get('error','B35 boundary checks failed'))
    except Exception as error:
        failure=dict(passed=False,error=str(error),robot_commands_sent=False)
        try:failure['board_diagnostics']=collect_diagnostics(board,profile)
        except Exception as diagnostic_error:failure['diagnostic_error']=str(diagnostic_error)
        try:failure['restored_service']=request('ensure',source='host_observation')
        except Exception as restore_error:failure['restore_error']=str(restore_error)
        report_path.write_text(json.dumps(failure,ensure_ascii=False,indent=2)+'\n')
        print('BOOTH_START_FAILED='+json.dumps(failure,ensure_ascii=False),flush=True)
        return 1
    report=json.loads(report_path.read_text());last=report['checks'][-1]
    remote=profile['board_root']
    expected={key:last[key] for key in ('mask','seq','session','value')}
    code="""from pathlib import Path
import json,time
path=Path(%r)/'runs/booth_lcd.json'
expected=%r
deadline=time.monotonic()+3
while True:
 try:state=json.loads(path.read_text())
 except (OSError,ValueError):state={}
 if all(state.get(k)==v for k,v in {'mask':expected['mask'],'sequence':expected['seq'],'session':expected['session'],'value':expected['value']}.items()):break
 if time.monotonic()>=deadline:raise RuntimeError('LCD state does not match the last verified PL reply')
 time.sleep(.1)
print('BOOTH_LCD_STATE='+json.dumps(state))
"""%(remote,expected)
    try:output=checked_execute(board,code)
    except Exception as error:
        report.update(passed=False,pl_checks_passed=True,lcd_state_match=False,error=str(error))
        try:report['board_diagnostics']=collect_diagnostics(board,profile)
        except Exception as diagnostic_error:report['diagnostic_error']=str(diagnostic_error)
        try:report['restored_service']=request('ensure',source='host_observation')
        except Exception as restore_error:report['restore_error']=str(restore_error)
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print('BOOTH_DISPLAY_FAILED='+json.dumps(report,ensure_ascii=False),flush=True)
        return 1
    report['board_lcd_state']=json.loads(next(s.split('=',1)[1] for s in output.splitlines() if s.startswith('BOOTH_LCD_STATE=')))
    report['board_lcd_state_match']=True
    prefix=str(Path(remote).relative_to('/home/xilinx/jupyter_notebooks'))
    try:
        screenshot=board.request('GET','/api/contents/'+prefix+'/runs/booth_lcd.png').json()
        image=base64.b64decode(screenshot['content'])
        if hashlib.sha256(image).hexdigest()!=report['board_lcd_state']['image_sha256']:
            raise RuntimeError('LCD screenshot does not match the verified display state')
        (folder/'board_lcd.png').write_bytes(image)
    except Exception as error:
        report.update(passed=False,pl_checks_passed=True,lcd_image_match=False,error=str(error))
        try:report['board_diagnostics']=collect_diagnostics(board,profile)
        except Exception as diagnostic_error:report['diagnostic_error']=str(diagnostic_error)
        try:report['restored_service']=request('ensure',source='host_observation')
        except Exception as restore_error:report['restore_error']=str(restore_error)
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print('BOOTH_DISPLAY_FAILED='+json.dumps(report,ensure_ascii=False),flush=True)
        return 1
    report['lcd_image_match']=True
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('BOOTH_B35_READY: real PL boundary checks and LCD state matched; physical panel still needs visual confirmation. Evidence: '+str(report_path),flush=True)
    if args.verify_only:return 0
    print('等待验证会话释放后打开互动页面；FPGA 服务保持运行。',flush=True)
    time.sleep(5.2)
    os.execv(sys.executable,[sys.executable,str(ROOT/'booth.py'),'--fpga','192.168.2.99','--port',str(args.port),
                           *(['--no-browser'] if args.no_browser else []),
                           *(['--auto-leader-can'] if args.auto_leader_can else []),
                           *(['--leader-can',args.leader_can] if args.leader_can else [])])


if __name__=='__main__':raise SystemExit(main())
