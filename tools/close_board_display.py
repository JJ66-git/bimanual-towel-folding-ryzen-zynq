#!/usr/bin/env python3
"""Close the owned B35 LCD renderer while retaining the UDP checking service.

No camera/CAN/robot APIs. A no-LCD service mode prevents a renderer from turning
the framebuffer on again. Validate the loaded guard and LCD before MMIO writes.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];PROJECT=ROOT
sys.path[:0]=[str(PROJECT/'npu_deploy'),str(PROJECT/'npu_deploy/fpga_board'),str(PROJECT/'npu_deploy/fpga_authority')]

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    from board_python import ensure_board_python
    ensure_board_python(__file__)
    from operation_lock_path import operation_lock
    from jupyter_client import JupyterBoard
    from control_jupyter import request,PROFILE
    from benchmark import connect
    from host.reference import load_profile
    from host.fold_protocol import BUILD_ID
    board=JupyterBoard();profile=json.loads(PROFILE.read_text());remote=profile['board_root']
    with operation_lock(PROJECT):
        before=request('status',board_client=board)
        state=request('ensure',source='host_observation',lcd=False,bulk_vectors=True,board_client=board)
        client=connect(('192.168.2.99',9009),load_profile());client.close()
        # download=False only attaches to current MMIO; it does not load a bitstream.
        inner="""import json
from pynq import Overlay
from board.fpg1_v3_server import DEFAULT_BITFILE
overlay=Overlay(str(DEFAULT_BITFILE),download=False)
assert overlay.guard.read(0)==%r,'Guard identity mismatch'
assert overlay.lcd.read(0)==0x4c434431,'LCD1 identity mismatch'
before=int(overlay.lcd.read(4));overlay.lcd.write(4,0)
after=int(overlay.lcd.read(4));assert after&1==0,'LCD stayed enabled'
print('LCD_OFF='+json.dumps(dict(control_before=before,control_after=after,guard_build=int(overlay.guard.read(0)),lcd_enabled=False)))
"""%BUILD_ID
        code="""import sys,os,subprocess
environment=os.environ.copy();environment['PYTHONPATH']=%r
result=subprocess.run([sys.executable,'-c',%r],cwd=%r,env=environment,capture_output=True,text=True,timeout=20)
print(result.stdout)
if result.returncode:raise RuntimeError(result.stderr)
"""%(remote,inner,remote)
        response=board.execute(code)
        if not response['passed']:raise RuntimeError(str(response['errors']))
        lcd=json.loads(next(row.split('=',1)[1] for row in response['output'].splitlines() if row.startswith('LCD_OFF=')))
        after=request('status',board_client=board)
        assert after['state']=='running' and after['lcd'] is False
        # Check protocol again after touching only the LCD control register.
        from fpga_service_probe import probe
        probe('192.168.2.99:9009')
        report=dict(passed=True,lcd=lcd,before=before,after=after,udp_probe_passed=True,robot_commands_sent=False)
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(report,indent=2)+'\n')
    print('BOARD_DISPLAY_CLOSED='+json.dumps(dict(passed=True,lcd_enabled=False,service=after['state'],report=str(args.out),robot_commands_sent=False)))
    return 0

if __name__=='__main__':raise SystemExit(main())
