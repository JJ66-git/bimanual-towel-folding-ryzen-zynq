#!/usr/bin/env python3
"""Read-only Jupyter snapshot of the selected runtime before any deployment."""
import argparse
from datetime import datetime
import hashlib
import http.cookiejar
import json
from pathlib import Path
import sys
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[1]
PROJECT=ROOT
sys.path.insert(0,str(PROJECT/'npu_deploy/fpga_board'))


def snapshot(board,profile,out):
    remote=profile['board_root'];prefix=str(Path(remote).relative_to('/home/xilinx/jupyter_notebooks'))
    if not prefix.startswith('fpga_fpg1_v3_b35_') or '..' in Path(prefix).parts:
        raise ValueError('Only the selected versioned B35 runtime may be captured')
    out=Path(out)
    if (out/'board_snapshot.json').exists():raise FileExistsError('Retain the existing snapshot; choose a new directory')
    out.mkdir(parents=True,exist_ok=True);rows=[]
    def get(path,content=0):
        response=board.request('GET','/api/contents/'+quote(prefix+'/'+path,safe='/'),params={'content':content})
        return response.json()
    def read_file(path,meta,dynamic=False):
        data=board.request('GET','/files/'+quote(prefix+'/'+path,safe='/')).content
        if not dynamic and len(data)!=meta['size']:raise RuntimeError('File changed during snapshot: '+path)
        after=get(path)
        if not dynamic and (after['size'],after.get('last_modified'))!=(meta['size'],meta.get('last_modified')):
            raise RuntimeError('Runtime changed during snapshot: '+path)
        target=out/'files'/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        rows.append(dict(path=path,bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),dynamic=dynamic))
    def walk(path):
        if len(Path(path).parts)>12:raise RuntimeError('Unexpected board directory depth')
        listing=get(path,1)
        for item in listing['content']:
            name=item['name']
            if Path(name).name!=name or '/' in name or name in ('.','..'):raise ValueError('Invalid board entry name')
            if name in ('__pycache__','.git') or name.startswith('.'):continue
            child=path+'/'+name
            if item['type']=='directory':walk(child)
            elif item['type']=='file' and Path(name).suffix.lower() in ('.py','.json','.bit','.hwh','.otf','.txt','.md','.sv','.svh','.tcl','.xdc'):
                read_file(child,item)
    for path in ('board','host','screen','config','overlay'):walk(path)
    for path in ('runs/service_state.json','runs/lcd_latest.json','runs/booth_lcd.json'):
        try:meta=get(path)
        except Exception as error:
            if getattr(getattr(error,'response',None),'status_code',None)==404:continue
            raise
        read_file(path,meta,True)
    names={r['path'] for r in rows}
    for required in ('board/fpg1_v3_server.py','board/fold_bridge.py','board/service_control.py','host/fold_protocol.py','config/guard_profile.json'):
        if required not in names:raise RuntimeError('Incomplete board snapshot: '+required)
    if not any(r['path'].endswith('.bit') and 'b35' in r['path'] for r in rows):
        raise RuntimeError('Selected B35 bitstream is missing from board snapshot')
    report=dict(schema='flexifold_board_snapshot_v1',passed=True,captured_at=datetime.now().astimezone().isoformat(),
                board_root=remote,profile=profile,files=rows,read_only_http=True,board_code_executed=False,
                service_stopped=False,overlay_reprogrammed=False,robot_commands_sent=False)
    (out/'board_snapshot.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--auth-workspace',type=Path,help='reuse an existing local cookie in memory only')
    args=parser.parse_args()
    from jupyter_client import JupyterBoard
    board=JupyterBoard()
    if args.auth_workspace:
        cookie=args.auth_workspace/'npu_deploy/artifacts/fpga_board/private/jupyter_cookies.txt'
        if cookie.exists():
            board.cookies=http.cookiejar.MozillaCookieJar(str(cookie));board.cookies.load(ignore_discard=True,ignore_expires=True)
            board.session.cookies=board.cookies
    board.request('GET','/login',timeout=3)  # Preserve transport errors instead of calling them a password failure.
    if not board.authenticated():raise RuntimeError('No authenticated board session; no board files were modified')
    profile=json.loads((PROJECT/'npu_deploy/fpga_board/active_profile.json').read_text())
    report=snapshot(board,profile,args.out)
    print(json.dumps(dict(passed=report['passed'],files=len(report['files']),manifest=str(args.out/'board_snapshot.json'),robot_commands_sent=False)))
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print('BOARD_SNAPSHOT_FAILED='+json.dumps(dict(error=type(error).__name__+': '+str(error),board_modified=False,robot_commands_sent=False)))
        raise SystemExit(1)
