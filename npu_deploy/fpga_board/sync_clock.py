#!/usr/bin/env python3
"""Set the selected board wall clock from this PC, leaving all protocol monotonic clocks intact."""
import argparse,json,time
from pathlib import Path
from jupyter_client import JupyterBoard


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
 board=JupyterBoard()
 if not board.authenticated():board.login_dialog()
 baseline=board.execute("import time,json\nprint('CLOCK_BASELINE='+json.dumps({'wall_s':time.time(),'monotonic_s':time.monotonic()}))")
 if not baseline['passed']:raise RuntimeError(baseline['errors'])
 args.out.parent.mkdir(parents=True,exist_ok=True)
 # Capture immediately before sending; bound the board timestamp using the PC send/receive interval.
 host_before=time.time()
 code='''import time,json,subprocess
before=time.time();mono_before=time.monotonic()
zone=subprocess.run(['timedatectl','set-timezone','Asia/Shanghai'],capture_output=True,text=True,timeout=10)
if zone.returncode:raise RuntimeError(zone.stderr)
time.tzset()
time.clock_settime(time.CLOCK_REALTIME,%r+(time.monotonic()-mono_before))
record={'before_epoch_s':before,'after_epoch_s':time.time(),'monotonic_elapsed_s':time.monotonic()-mono_before,
 'board_local':time.strftime('%%Y-%%m-%%d %%H:%%M:%%S %%z'),'board_utc':time.strftime('%%Y-%%m-%%dT%%H:%%M:%%SZ',time.gmtime()),
 'timezone':'Asia/Shanghai','source':'manual synchronization from AI PC','robot_commands_sent':False}
print('CLOCK_SYNC_RESULT='+json.dumps(record))
'''%host_before
 result=board.execute(code,timeout=30);host_after=time.time()
 record={'host_before_epoch_s':host_before,'host_after_epoch_s':host_after,'execution':result}
 if result['passed']:
  item=json.loads(next(line.split('=',1)[1] for line in result['output'].splitlines() if line.startswith('CLOCK_SYNC_RESULT=')))
  lower=item['after_epoch_s']-host_after;upper=item['after_epoch_s']-host_before
  record.update(board=item,offset_interval_s=[lower,upper],passed=abs((lower+upper)/2)<=.5 and upper-lower<=1)
 else:record['passed']=False
 args.out.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
 print(result['output']);print('CLOCK_SYNC_PASSED='+str(record['passed']))
 if not record['passed']:raise RuntimeError('Board clock could not be verified')
 authenticated=board.authenticated();print('JUPYTER_AUTH_AFTER_CLOCK_STEP='+str(authenticated))
 if not authenticated:board.login_dialog()
 verify_before=time.time()
 verified=board.execute("import time,json\nprint('CLOCK_VERIFIED='+json.dumps({'wall_s':time.time(),'local':time.strftime('%Y-%m-%d %H:%M:%S %z')}))")
 verify_after=time.time()
 if not verified['passed']:raise RuntimeError(verified['errors'])
 check=json.loads(next(line.split('=',1)[1] for line in verified['output'].splitlines() if line.startswith('CLOCK_VERIFIED=')))
 if not verify_before-.5<=check['wall_s']<=verify_after+.5:raise RuntimeError('Fresh-session clock verification failed')
 record['fresh_session_verification']={'host_before_epoch_s':verify_before,'host_after_epoch_s':verify_after,**check}
 args.out.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n');print(verified['output'])
 return 0
if __name__=='__main__':raise SystemExit(main())
