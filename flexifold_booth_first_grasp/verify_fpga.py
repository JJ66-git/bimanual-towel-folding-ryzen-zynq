"""Real B35 boundary checks using the booth's exact client; no robot imports."""
import argparse
import datetime
import json
from pathlib import Path
import time
from booth import Booth
from host import fold_protocol as wire


def verify(booth):
    rows=[]
    for angle,mask in [(2650,1),(2540,0),(2550,1),(2540,0)]:
        result=booth.result(booth.sample_for_angle(angle))
        if (result['source']!='board' or result.get('build_id')!=wire.BUILD_ID
                or result['mask']!=mask or result['reference_match'] is not True):
            raise RuntimeError('B35 interactive check failed: '+json.dumps(result))
        rows.append(result)
    return rows


def verify_ready(booth,timeout=8):
    deadline=time.monotonic()+timeout
    while True:
        try:return verify(booth)
        except (TimeoutError,ConnectionError):
            remaining=deadline-time.monotonic()
            if remaining<=0:raise
            time.sleep(min(5.2,remaining))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fpga',default='192.168.2.99')
    parser.add_argument('--out',type=Path,default=Path(__file__).resolve().parent/'verification/latest_b35.json')
    args=parser.parse_args()
    booth=Booth(args.fpga,9009)
    report=dict(checked_at=datetime.datetime.now().astimezone().isoformat(),
                address=args.fpga,expected_build_id=hex(wire.BUILD_ID),
                robot_commands_sent=False,passed=False,physical_lcd_confirmation=False)
    try:
        # The preceding task's lease expires without reloading the overlay.
        report['checks']=verify_ready(booth)
        report['passed']=True
    except Exception as error:
        report['error']=str(error)
    finally:
        booth.collector.close()
        if booth.board_link:booth.board_link.close()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('BOOTH_B35_VERIFICATION='+json.dumps(report,ensure_ascii=False),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
