#!/usr/bin/env python3
"""Show a static physical LCD inspection pattern; restore with control_jupyter.py start."""
import argparse,json,secrets,sys
from pathlib import Path
from control_jupyter import request,PROFILE
from jupyter_client import JupyterBoard


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
 profile=json.loads(PROFILE.read_text());root=profile['board_root'];token=str(secrets.randbelow(9000)+1000)
 state=request('stop');assert state['state']=='stopped'
 inner='''from pathlib import Path
import json
from pynq import Overlay,PL
from PIL import Image,ImageDraw,ImageFont
from board.ownership import exclusive_owner
from board.clock_reference import configure_reference
from board.fpg1_v3_server import DEFAULT_BITFILE
from screen.lcd import Framebuffer
root=Path.cwd();out=root/'maintenance';out.mkdir(exist_ok=True)
with exclusive_owner():
 configure_reference()
 assert Path(PL.bitfile_name).resolve()==DEFAULT_BITFILE.resolve(),'Current PL differs; refusing to reprogram'
 overlay=Overlay(str(DEFAULT_BITFILE),download=False)
 assert overlay.guard.read(0)==EXPECTED_BUILD
 assert overlay.guard.read(0x4c)==0,'Unexpected watchdog state'
 display=Framebuffer(overlay.lcd)
 image=Image.new('RGB',(480,800),(5,17,29));draw=ImageDraw.Draw(image)
 font='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
 small=ImageFont.truetype(font,17);normal=ImageFont.truetype(font,22);title=ImageFont.truetype(font,36)
 white=(236,245,255)
 draw.rectangle((5,5,474,794),outline=white,width=2)
 draw.text((15,20),'TOP LEFT',fill=white,font=small);draw.text((350,20),'TOP RIGHT',fill=white,font=small)
 draw.text((28,78),'LCD CHECK',fill=white,font=title)
 draw.text((28,130),'TEST '+TOKEN,fill=(28,211,235),font=title)
 draw.text((28,185),'B35 / 480 x 800 / PORTRAIT',fill=white,font=normal)
 for i,(name,color) in enumerate([('RED',(255,0,0)),('GREEN',(0,255,0)),('BLUE',(0,0,255)),('WHITE',white)]):
  x=24+i*110;draw.rectangle((x,245,x+100,325),fill=color)
  draw.text((x,337),name,fill=white,font=small)
 for y in range(415,546,26):draw.line((24,y,455,y),fill=(118,142,163),width=1)
 for x in range(24,456,43):draw.line((x,415,x,545),fill=(118,142,163),width=1)
 draw.text((28,580),'Check: upright, full border,',fill=white,font=normal)
 draw.text((28,615),'colors, text, no flicker.',fill=white,font=normal)
 draw.text((28,680),'NO ROBOT COMMANDS',fill=(37,230,167),font=normal)
 draw.text((15,755),'BOTTOM LEFT',fill=white,font=small);draw.text((315,755),'BOTTOM RIGHT',fill=white,font=small)
 before=int(overlay.lcd.read(0x18));result=display.present(image)
 import time;time.sleep(.15)
 result.update(token=TOKEN,scan_delta=int(overlay.lcd.read(0x18))-before,build_id=hex(overlay.guard.read(0)),
  physical_display_verified=False,awaiting_human_confirmation=True,robot_commands_sent=False)
 image.save(out/'lcd_visual_check.png');(out/'lcd_visual_check.json').write_text(json.dumps(result,indent=2)+'\\n')
 print('LCD_VISUAL_CHECK='+json.dumps(result))
# Intentionally keep the LCD enabled with this static frame until service restoration.
'''.replace('EXPECTED_BUILD',str(int(profile['expected_build_id'],16))).replace('TOKEN',repr(token))
 code="import os,sys,subprocess\nenv=os.environ.copy();env['PYTHONPATH']=%r\np=subprocess.run([sys.executable,'-c',%r],cwd=%r,env=env,text=True,capture_output=True,timeout=20)\nprint(p.stdout)\nif p.returncode:raise RuntimeError(p.stderr)\n"%(root,inner,root)
 board=JupyterBoard();result=board.execute(code);args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,indent=2)+'\n')
 print(result['output'])
 if not result['passed']:
  request('start')
  raise RuntimeError(result['errors'])
 return 0
if __name__=='__main__':raise SystemExit(main())
