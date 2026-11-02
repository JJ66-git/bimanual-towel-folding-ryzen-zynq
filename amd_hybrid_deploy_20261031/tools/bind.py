"""Update only this copy's arm USB identities; never sends CAN or discovers roles by guessed numbering."""
import argparse, json
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--left-serial',required=True)
    p.add_argument('--right-serial',required=True)
    args=p.parse_args()
    if not args.left_serial or not args.right_serial or args.left_serial==args.right_serial:
        p.error('Two distinct USB serial numbers required')
    path=Path(__file__).resolve().parents[1]/'piper_towel_replica/configs/cobot_site.json'
    site=json.loads(path.read_text())
    backup=path.with_name('cobot_site.before_target_binding.json')
    if not backup.exists(): backup.write_text(json.dumps(site,ensure_ascii=False,indent=2)+'\n')
    site['usb_serials']={'left':args.left_serial,'right':args.right_serial}
    path.write_text(json.dumps(site,ensure_ascii=False,indent=2)+'\n')
    print('Saved USB identities; visually bind cameras separately. No device access.')

if __name__=='__main__': main()
