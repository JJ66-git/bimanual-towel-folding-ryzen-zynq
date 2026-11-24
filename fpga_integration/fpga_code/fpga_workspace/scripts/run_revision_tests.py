#!/usr/bin/env python3
"""Run original six RTL benches plus the two defect regressions in the B35 tree."""
import json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TOOLS=ROOT.parents[3]/'downloads/rtl_tools/runtime'
# Override location through CLI for machines other than this workstation.
import argparse
p=argparse.ArgumentParser();p.add_argument('--tools',type=Path,default=Path('/home/ysyx/FPGA_competition/downloads/rtl_tools/runtime'));p.add_argument('--out',type=Path,required=True);args=p.parse_args()
lib=next((args.tools/'usr/lib').glob('*/ivl'));args.out.mkdir(parents=True,exist_ok=True)
rtl=ROOT/'rtl';modules=['motion_guard.sv','link_watchdog.sv','fold_stage.sv','fold_vision.sv','axi_lite_guard.sv','axi_lcd.sv']
benches=[('tb_link_watchdog','tb_link_watchdog'),('tb_fold_stage','tb_fold_stage'),('tb_fold_vision','tb_fold_vision'),('tb_axi_lite_guard','tb_axi_lite_guard'),('tb_motion_guard','tb_motion_guard'),('tb_axi_lcd','tb_axi_lcd'),('tb_retreat_expiry_regression','tb_fold_stage'),('tb_frame_gap_regression','tb_fold_vision')]
rows=[]
for file,top in benches:
 output=args.out/(file+'.vvp')
 command=[str(args.tools/'usr/bin/iverilog'),'-B'+str(lib),'-g2012','-s',top,'-I'+str(rtl),'-o',str(output),*[str(rtl/f) for f in modules],str(rtl/(file+'.sv'))]
 compiled=subprocess.run(command,capture_output=True,text=True)
 (args.out/(file+'.compile.log')).write_text(compiled.stdout+compiled.stderr)
 if compiled.returncode:rows.append({'test':file,'passed':False,'phase':'compile'});continue
 try:result=subprocess.run([str(args.tools/'usr/bin/vvp'),'-M'+str(lib),str(output)],cwd=rtl,capture_output=True,text=True,timeout=180)
 except subprocess.TimeoutExpired:
  rows.append({'test':file,'passed':False,'phase':'timeout'});continue
 (args.out/(file+'.run.log')).write_text(result.stdout+result.stderr)
 row={'test':file,'passed':result.returncode==0 and 'PASS' in result.stdout,'returncode':result.returncode,'output':result.stdout[-1300:]};rows.append(row)
 print(json.dumps(row),flush=True)
(args.out/'summary.json').write_text(json.dumps(rows,indent=2)+'\n')
raise SystemExit(0 if all(r['passed'] for r in rows) else 1)
