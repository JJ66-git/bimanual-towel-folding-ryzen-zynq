#!/usr/bin/env python3
"""Read only the selected board's bounded service, console and display logs."""
import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[1]
MANAGEMENT=ROOT/'npu_deploy/fpga_board'
sys.path.insert(0,str(MANAGEMENT))


def diagnose(board,profile):
    remote=profile['board_root'];prefix=str(Path(remote).relative_to('/home/xilinx/jupyter_notebooks'))
    if not re.fullmatch(r'fpga_fpg1_v3_b35_[A-Za-z0-9_]+',prefix):raise ValueError('Unexpected board root')
    def read(name):
        response=board.request('GET','/files/'+quote(prefix+'/'+name,safe='/'),headers={'Range':'bytes=-65536'})
        return response.content[-65536:].decode('utf-8',errors='replace')
    state=json.loads(read('runs/service_state.json'))
    report=dict(service_state=state,board_modified=False,robot_commands_sent=False)
    for key in ('log','console'):
        selected=state.get(key,'')
        if not re.fullmatch(re.escape(remote)+r'/runs/server_[0-9]+\.'+('jsonl' if key=='log' else 'log'),selected):
            report[key+'_error']='No selected service log';continue
        try:report[key+'_tail']=read(str(Path(selected).relative_to(remote)))
        except Exception as error:report[key+'_error']=str(error)
    for name in ('lcd_latest','booth_lcd','lcd_worker_startup','service_health'):
        try:report[name]=json.loads(read('runs/'+name+'.json'))
        except Exception as error:report[name+'_error']=str(error)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    from jupyter_client import JupyterBoard
    board=JupyterBoard();profile=json.loads((MANAGEMENT/'active_profile.json').read_text())
    report=diagnose(board,profile);args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('BOARD_DIAGNOSTICS='+json.dumps(dict(report=str(args.out),console_tail=report.get('console_tail','')[-3000:],journal_tail=report.get('log_tail','')[-3000:],board_modified=False,robot_commands_sent=False),ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
