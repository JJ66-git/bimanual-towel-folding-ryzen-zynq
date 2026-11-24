#!/usr/bin/env python3
"""Hash-verified B35 static-file restore with automatic roll-forward on failure.

Default is a local read-only plan. Dynamic logs, old PIDs and lifecycle state are
never restored. Applying takes the shared rig lock and snapshots the current
board first. It never imports a robot API or sends CAN commands.
"""
import argparse
import hashlib
import json
from pathlib import Path,PurePosixPath
import re
import sys
import time
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[1];PROJECT=ROOT
sys.path[:0]=[str(PROJECT/'npu_deploy'),str(PROJECT/'npu_deploy/fpga_board'),str(ROOT/'tools')]


def payloads(snapshot):
    snapshot=Path(snapshot).resolve();report=json.loads((snapshot/'board_snapshot.json').read_text())
    if report.get('schema')!='flexifold_board_snapshot_v1' or report.get('passed') is not True:raise ValueError('Unverified board snapshot')
    if not re.fullmatch(r'/home/xilinx/jupyter_notebooks/fpga_fpg1_v3_b35_[A-Za-z0-9_]+',report['board_root']):raise ValueError('Unexpected board root')
    result={}
    for row in report['files']:
        name=PurePosixPath(row['path'])
        if name.is_absolute() or '..' in name.parts or not name.parts:raise ValueError('Unsafe board snapshot path')
        if row.get('dynamic'):continue
        if name.parts[0] not in ('board','host','screen','config','overlay'):raise ValueError('Unexpected static board file')
        path=snapshot/'files'/str(name)
        if any(parent.is_symlink() for parent in (path,*path.parents) if parent!=snapshot.parent and snapshot in (parent,*parent.parents)):
            raise ValueError('Snapshot payload must not traverse a symlink')
        data=path.read_bytes()
        if len(data)!=row['bytes'] or hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('Board snapshot integrity failed: '+str(name))
        if str(name) in result:raise ValueError('Duplicate board snapshot entry')
        result[str(name)]=data
    if not result:raise ValueError('No static files to restore')
    return report,result


def apply_restore(board,profile,snapshot,control,capture,*,recovery_directory,verify_ready):
    manifest,desired=payloads(snapshot)
    if manifest['board_root']!=profile['board_root']:raise ValueError('Snapshot and selected release differ')
    prefix=str(Path(profile['board_root']).relative_to('/home/xilinx/jupyter_notebooks'))
    def read(name):
        try:return board.request('GET','/files/'+quote(prefix+'/'+name,safe='/')).content
        except Exception as error:
            if getattr(getattr(error,'response',None),'status_code',None)==404:return None
            raise
    current={name:read(name) for name in desired};changed=[name for name in desired if desired[name]!=current[name]]
    if not changed:return dict(passed=True,changed=[],service_reused=True,robot_commands_sent=False)
    previous=control('status');capture(board,profile,recovery_directory)
    old_state=Path(snapshot)/'files/runs/service_state.json'
    selected=json.loads(old_state.read_text()) if old_state.exists() else {}
    def restart(state):
        if previous['state']=='running':
            control('ensure',source=state.get('source') or 'host_observation',lcd=state.get('lcd',True),isolated_lcd=state.get('isolated_lcd',False),bulk_vectors=state.get('bulk_vectors',False))
            verify_ready()
        else:control('stop')
    try:
        control('stop')
        for name in changed:board.upload(prefix+'/'+name,desired[name])
        if any(read(name)!=desired[name] for name in changed):raise RuntimeError('Restored bytes failed verification')
        restart(selected)
    except BaseException as original:
        try:
            control('stop')
            for name in changed:
                if current[name] is not None:board.upload(prefix+'/'+name,current[name])
                else:
                    path=str(Path(profile['board_root'])/name)
                    result=board.execute('from pathlib import Path\np=Path(%r)\nif p.is_file():p.unlink()\n'%path)
                    if not result.get('passed'):raise RuntimeError('Could not remove newly restored file')
            if any(read(name)!=current[name] for name in changed):raise RuntimeError('Roll-forward verification failed')
            restart(previous)
        except BaseException as error:raise RuntimeError('Restore failed and roll-forward needs recovery from '+str(recovery_directory)+': '+str(error)) from original
        raise
    return dict(passed=True,changed=changed,recovery_snapshot=str(recovery_directory),robot_commands_sent=False)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--snapshot',type=Path,required=True)
    p.add_argument('--apply',action='store_true');args=p.parse_args();manifest,data=payloads(args.snapshot)
    if not args.apply:
        print(json.dumps(dict(mode='read_only_plan',passed=True,files=len(data),bytes=sum(map(len,data.values())),
            board_root=manifest['board_root'],dynamic_state_restored=False,robot_commands_sent=False),indent=2));return 0
    from board_python import ensure_board_python
    ensure_board_python(__file__)
    from operation_lock_path import operation_lock
    from jupyter_client import JupyterBoard
    from control_jupyter import request,PROFILE
    from snapshot_board import snapshot as capture
    profile=json.loads(PROFILE.read_text());board=JupyterBoard()
    def control(operation,**kwargs):return request(operation,board_client=board,**kwargs)
    def ready():
        sys.path.insert(0,str(PROJECT/'npu_deploy/fpga_authority'))
        from benchmark import connect
        from host.reference import load_profile
        client=connect(('192.168.2.99',9009),load_profile());client.close()
    with operation_lock(PROJECT):
        result=apply_restore(board,profile,args.snapshot,control,capture,
            recovery_directory=ROOT/'runtime_snapshots/restore'/str(time.time_ns()),verify_ready=ready)
    print(json.dumps(result,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
