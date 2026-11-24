"""Bounded LCD test; restore packaged B32 in finally. No robot interfaces."""
import argparse
import json
import signal
from pathlib import Path
import time

from PIL import Image, ImageDraw, ImageFont
from screen.lcd import Framebuffer
from board.ownership import exclusive_owner

ROOT=Path(__file__).resolve().parents[1]


def test_image():
    image=Image.new('RGB',(480,800),(5,17,29))
    draw=ImageDraw.Draw(image)
    font=ImageFont.load_default()

    def label(x,y,value,scale=4):
        mask=font.getmask(value)
        alpha=Image.frombytes('L',mask.size,bytes(mask))
        width,height=alpha.width*scale,alpha.height*scale
        if width>440:
            height=int(height*440/width); width=440
        image.paste((236,245,255),(x,y),alpha.resize((width,height),Image.NEAREST))

    label(20,30,'PYTHON LCD TEST',5)
    label(20,98,'LCD1 + B32 / PORTRAIT',3)
    for y,color,name in [(180,(255,0,0),'RED'),(290,(0,255,0),'GREEN'),(400,(0,0,255),'BLUE')]:
        draw.rectangle((20,y,460,y+80),fill=color)
        label(32,y+22,name,4)
    draw.polygon(((240,510),(155,605),(205,605),(205,690),(275,690),(275,605),(325,605)),fill=(28,211,235))
    label(200,710,'UP',5)
    label(22,770,'DISPLAY TEST / NO ROBOT CONTROL',2)
    return image


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log',type=Path,required=True,help='new JSONL file')
    args=parser.parse_args()
    combined=ROOT/'overlay/lcd_framebuffer_20261101/lcd1_guard.bit'
    baseline=ROOT/'overlay/own_hybrid_motion_guard_watchdog.bit'
    for path in (combined,combined.with_suffix('.hwh'),baseline,baseline.with_suffix('.hwh')):
        if not path.is_file(): parser.error('missing file: {}'.format(path))
    if args.log.exists() or not args.log.parent.is_dir():
        parser.error('log must be a new file in an existing directory')
    with exclusive_owner():
        from pynq import Overlay
        from board.pynq_udp_server import PynqGuard
        def stop(signum,frame):raise KeyboardInterrupt
        signal.signal(signal.SIGTERM,stop)
        signal.signal(signal.SIGINT,stop)
        with args.log.open('x',encoding='utf-8',buffering=1) as output:
            def emit(event,**data):
                row=dict(event=event,perf_ns=time.perf_counter_ns(),**data)
                line=json.dumps(row,sort_keys=True)
                output.write(line+'\n'); print(line,flush=True)
            try:
                overlay=Overlay(str(combined))
                guard=PynqGuard(overlay.guard)
                guard.write(0x4c,0)
                emit('combined_loaded',guard_build=guard.read(0),watchdog_status=guard.read(0x54))
                time.sleep(3)
                lcd=Framebuffer(overlay.lcd)
                emit('python_frame_submitted',**lcd.present(test_image()))
                time.sleep(180)
            finally:
                restored=Overlay(str(baseline))
                guard=PynqGuard(restored.guard)
                guard.write(0x4c,0)
                emit('baseline_restored',guard_build=guard.read(0),
                     watchdog_control=guard.read(0x4c),watchdog_status=guard.read(0x54))


if __name__=='__main__':
    main()
