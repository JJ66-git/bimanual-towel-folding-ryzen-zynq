"""Read wall-clock alignment and separately recorded human LCD evidence."""
import json,time


def inspect(board,root,build_id):
 code='''import json,time
from pathlib import Path
confirmation=Path(%r)/'maintenance/lcd_visual_confirmation.json'
metadata={'epoch_s':time.time(),'timezone':Path('/etc/timezone').read_text().strip(),
 'mac':Path('/sys/class/net/eth0/address').read_text().strip(),
 'lcd_confirmation':json.loads(confirmation.read_text()) if confirmation.exists() else None}
print('BOARD_MAINTENANCE_STATE='+json.dumps(metadata))
'''%root
 before=time.time();result=board.execute(code);after=time.time()
 if not result['passed']:raise RuntimeError(result['errors'])
 metadata=json.loads(next(line.split('=',1)[1] for line in result['output'].splitlines() if line.startswith('BOARD_MAINTENANCE_STATE=')))
 clock={'passed':before-.5<=metadata['epoch_s']<=after+.5 and after-before<2 and metadata['timezone']=='Asia/Shanghai',
  'board_epoch_s':metadata['epoch_s'],'timezone':metadata['timezone'],'estimated_board_minus_pc_s':metadata['epoch_s']-(before+after)/2,
  'measurement_round_trip_s':after-before,'method':'read-only Jupyter time comparison; manual synchronization, not NTP certification'}
 visual=metadata['lcd_confirmation'] or {}
 matched=bool(visual.get('physical_display_verified')) and visual.get('build_id')==build_id and visual.get('board_mac')==metadata['mac'] and visual.get('panel_id')=='0x4384'
 lcd={'passed':matched,'confirmation_source':visual.get('confirmation_source'),'confirmed_at':visual.get('confirmed_at'),
  'inspection_token':visual.get('inspection_token'),'build_id':visual.get('build_id'),'board_mac':metadata['mac'],'checks':visual.get('checks',[])}
 return {'fpga_board_clock':clock,'fpga_lcd_visual_confirmation':lcd}
