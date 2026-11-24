#!/usr/bin/env python3
"""Manage only the versioned B34 service through the user's authenticated Jupyter."""
import argparse
import json
from pathlib import Path
import sys
import time
from jupyter_client import JupyterBoard

PROFILE=Path(__file__).with_name('active_profile.json')


def ensure_service(source='host_observation',lcd=True,profile_path=None,requester=None,isolated_lcd=False,bulk_vectors=False):
    call=requester or request
    state=call('status',source,lcd,profile_path)
    if state['state']=='running' and (state.get('source')!=source or state.get('lcd',True)!=lcd or state.get('isolated_lcd',False)!=bool(isolated_lcd and lcd) or state.get('bulk_vectors',False)!=bulk_vectors):
        call('stop',source,lcd,profile_path)
    options={}
    if isolated_lcd and lcd:options['isolated_lcd']=True
    if bulk_vectors:options['bulk_vectors']=True
    return call('start',source,lcd,profile_path,**options)


def request(operation,source='host_observation',lcd=True,profile_path=None,isolated_lcd=False,bulk_vectors=False,board_client=None):
    if operation=='ensure':
        call=None
        if board_client is not None:
            call=lambda op,src,screen,profile,**kwargs:request(op,src,screen,profile,board_client=board_client,**kwargs)
        return ensure_service(source,lcd,profile_path,requester=call,isolated_lcd=isolated_lcd,bulk_vectors=bulk_vectors)
    board=board_client or JupyterBoard()
    board.request('GET','/login')
    if not board.authenticated():raise RuntimeError('Jupyter login expired; run jupyter_client.py --login on this PC')
    profile=json.loads(Path(profile_path or PROFILE).read_text())
    root=profile['board_root']
    import re
    if not re.fullmatch(r'/home/xilinx/jupyter_notebooks/fpga_fpg1_v3_b[0-9]+_[A-Za-z0-9_]+',root):
        raise ValueError('Unexpected versioned board root')
    # A fresh process avoids cached board/host modules from another Notebook release.
    extra=',isolated_lcd=True' if isolated_lcd and lcd and operation=='start' else ''
    if bulk_vectors and operation=='start':extra+=',bulk_vectors=True'
    inner="import json;from board.service_control import control;print('B34_CONTROL_RESULT='+json.dumps(control(%r,source=%r,lcd=%r%s)))"%(operation,source,lcd,extra)
    code='''import sys,os,subprocess
environment=os.environ.copy();environment['PYTHONPATH']=%r
completed=subprocess.run([sys.executable,'-c',%r],cwd=%r,env=environment,capture_output=True,text=True,timeout=20)
print(completed.stdout)
if completed.returncode:raise RuntimeError(completed.stderr)
'''%(root,inner,root)
    result=board.execute(code)
    if not result['passed']:raise RuntimeError(str(result['errors']))
    for line in result['output'].splitlines():
        if line.startswith('B34_CONTROL_RESULT='):
            response=json.loads(line.split('=',1)[1])
            if response.get('root')!=root:raise RuntimeError('Management response is from another release')
            return response
    raise RuntimeError('No matched B34 control response')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['start','ensure','status','finish','stop'])
    parser.add_argument('--source',choices=['synthetic_test','recorded_replay','host_observation'],default='host_observation')
    parser.add_argument('--no-lcd',action='store_true')
    parser.add_argument('--isolated-lcd',action='store_true')
    parser.add_argument('--bulk-vectors',action='store_true')
    parser.add_argument('--release-profile',type=Path)
    args=parser.parse_args()
    if args.operation=='status':return run_operation(args)
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from operation_lock_path import operation_lock
    with operation_lock():return run_operation(args)


def run_operation(args):
    if args.operation=='ensure':
        import importlib.util
        project=Path(__file__).resolve().parents[2]
        helper=project/'flexifold_booth_first_grasp/prepare_hardware.py'
        spec=importlib.util.spec_from_file_location('paired_display_deploy',helper)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        board=JupyterBoard()
        board.request('GET','/login')
        if not board.authenticated():board.login_dialog()
        profile=json.loads(Path(args.release_profile or PROFILE).read_text())
        module.deploy(board,profile,lambda operation:request(operation,profile_path=args.release_profile,board_client=board))
    result=request(args.operation,args.source,not args.no_lcd,args.release_profile,isolated_lcd=args.isolated_lcd,bulk_vectors=args.bulk_vectors)
    print('FPGA_JUPYTER_CONTROL='+json.dumps(result),flush=True)
    if args.operation in ('start','ensure'):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        from fpga_service_probe import probe
        probe('192.168.2.99:9009')
    return 0

if __name__=='__main__':raise SystemExit(main())
