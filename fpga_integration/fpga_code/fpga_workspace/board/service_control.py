"""Version-scoped B34 start/status/finish for an authenticated Jupyter kernel.

Never reads lifecycle keys, touches CAN, or kills an unrelated process.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
import hashlib

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'runs/service_state.json'


def implementation_fingerprint(bitfile):
    names=('board/service_control.py','board/fpg1_v3_server.py','board/fold_bridge.py','board/isolated_display.py',
           'host/fold_protocol.py','host/protocol.py','host/reference.py','host/fold_config.py','host/action_status.py',
           'screen/lcd.py','screen/cooperative_state.py','screen/cooperative_view.py',
           'screen/assets/DisplaySansSC.otf','screen/assets/DisplaySansSC-Bold.otf',
           'config/guard_profile.json','config/fold_verify_profile.json')
    files={name:ROOT/name for name in names}
    files.update(bitfile=Path(bitfile),hwh=Path(bitfile).with_suffix('.hwh'))
    return {name:hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None for name,path in files.items()}


def _save(state):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    temporary=STATE.with_suffix('.tmp');temporary.write_text(json.dumps(state,indent=2)+'\n');temporary.replace(STATE)


def _owned(state):
    pid=state.get('pid')
    if not isinstance(pid,int):return False
    try:
        raw=(Path('/proc')/str(pid)/'cmdline').read_bytes()
        stat=(Path('/proc')/str(pid)/'stat').read_text().split()
        return (b'board.fpg1_v3_server' in raw and str(ROOT).encode() in raw
                and state.get('start_ticks')==stat[21])
    except OSError:return False


def control(operation,peer='192.168.2.10',source='host_observation',lcd=True,isolated_lcd=False,bulk_vectors=False):
    state=json.loads(STATE.read_text()) if STATE.exists() else {}
    alive=_owned(state)
    if operation=='status':return dict(state='running' if alive else 'stopped',pid=state.get('pid'),start_ticks=state.get('start_ticks'),root=str(ROOT),source=state.get('source'),lcd=state.get('lcd',True),isolated_lcd=state.get('isolated_lcd',False),bulk_vectors=state.get('bulk_vectors',False),implementation_sha256=state.get('implementation_sha256'))
    if operation=='start':
        if alive:
            if state.get('source')!=source:raise RuntimeError('Owned B34 service uses another source mode; stop it explicitly before switching')
            if state.get('lcd',True)!=lcd:raise RuntimeError('Owned service uses another LCD enable mode; ensure it explicitly')
            if state.get('isolated_lcd',False)!=isolated_lcd:raise RuntimeError('Owned service uses another LCD worker mode; ensure it explicitly')
            if state.get('bulk_vectors',False)!=bulk_vectors:raise RuntimeError('Owned service uses another MMIO mode; ensure it explicitly')
            state['stop_token']=None;_save(state)
            return {'state':'running','pid':state['pid'],'root':str(ROOT)}
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as check:
            check.bind(('0.0.0.0',9009))  # Fail on another owner instead of replacing it.
        from board.fpg1_v3_server import DEFAULT_BITFILE
        STATE.parent.mkdir(parents=True,exist_ok=True)
        stamp=str(time.time_ns());log=STATE.parent/('server_'+stamp+'.jsonl')
        console=STATE.parent/('server_'+stamp+'.log')
        command=[sys.executable,'-m','board.fpg1_v3_server','--bitfile',str(DEFAULT_BITFILE),
                 '--peer',peer,'--log',str(log),'--source',source]
        if lcd:command+=['--lcd']
        if isolated_lcd and lcd:command+=['--isolated-lcd']
        if bulk_vectors:command+=['--bulk-vectors']
        if source=='recorded_replay':command+=['--booth']
        environment=os.environ.copy();environment['PYTHONPATH']=str(ROOT)
        with console.open('wb') as out:
            process=subprocess.Popen(command,cwd=str(ROOT),env=environment,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
        state={'pid':process.pid,'start_ticks':(Path('/proc')/str(process.pid)/'stat').read_text().split()[21],
               'source':source,'lcd':bool(lcd),'isolated_lcd':bool(isolated_lcd and lcd),'bulk_vectors':bool(bulk_vectors),
               'implementation_sha256':implementation_fingerprint(DEFAULT_BITFILE),'log':str(log),'console':str(console),'stop_token':None}
        _save(state)
        return {'state':'starting','pid':process.pid,'root':str(ROOT),'console':str(console)}
    if operation in ('finish','stop'):
        if not alive:return {'state':'stopped','root':str(ROOT)}
        if operation=='stop':
            os.kill(state['pid'],signal.SIGINT)
            deadline=time.monotonic()+10
            while _owned(state) and time.monotonic()<deadline:time.sleep(.05)
            if _owned(state):raise RuntimeError('Owned B34 service did not stop; no unrelated process was killed')
            return {'state':'stopped','root':str(ROOT)}
        token=secrets.token_hex(16);state['stop_token']=token;_save(state)
        environment=os.environ.copy();environment['PYTHONPATH']=str(ROOT)
        subprocess.Popen([sys.executable,'-m','board.service_control','--scheduled-stop',token],cwd=str(ROOT),
                         env=environment,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        return {'state':'stop_scheduled_10s','pid':state['pid'],'root':str(ROOT)}
    raise ValueError('Unknown control operation')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--scheduled-stop',required=True)
    args=parser.parse_args();time.sleep(10)
    state=json.loads(STATE.read_text()) if STATE.exists() else {}
    if state.get('stop_token')==args.scheduled_stop and _owned(state):os.kill(state['pid'],signal.SIGINT)

if __name__=='__main__':main()
