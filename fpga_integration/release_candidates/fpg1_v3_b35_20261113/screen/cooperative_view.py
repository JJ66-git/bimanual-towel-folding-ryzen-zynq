"""480x800 portrait cooperative display, for the native 800x480 4.3-inch panel."""
from functools import lru_cache
from pathlib import Path
import time
from PIL import Image,ImageDraw,ImageFont

PALETTE=((242,238,226),(25,28,24),(233,76,44),(194,220,99),(224,218,201),(92,93,81),(255,250,238),(55,59,51),
 (164,174,135),(246,174,86),(188,52,34),(206,199,181),(233,222,179),(93,115,55),(255,255,255),(0,0,0))
PAPER,INK,ORANGE,LIME,LINE,MUTED,WHITE,DARK,GREEN,AMBER,RED,SHADE,SAND,OLIVE,_,_=PALETTE
FONT=Path(__file__).with_name('assets')/'DisplaySansSC.otf'

@lru_cache(maxsize=36)
def font(size,bold=False):
 selected=FONT.with_name('DisplaySansSC-Bold.otf') if bold else FONT
 if selected.exists():return ImageFont.truetype(str(selected),size)
 fallback=Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
 if fallback.exists():return ImageFont.truetype(str(fallback),size,index=2)
 return ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',size)

def render(state):
 im=Image.new('RGB',(480,800),PAPER);d=ImageDraw.Draw(im)
 def text(x,y,value,size=16,fill=INK,maxw=None):
  value=str(value);f=font(size,size>=22)
  mask=f.getmask(value,mode='L')
  if maxw:
   while value and mask.size[0]>maxw:
    value=value[:-1];mask=f.getmask(value,mode='L')
  if mask.size[0] and mask.size[1]:
   alpha=Image.frombytes('L',mask.size,bytes(mask));im.paste(fill,(x,y),alpha)
 def center(cx,y,value,size,fill=INK):
  width=font(size,size>=22).getmask(str(value),mode='L').size[0];text(round(cx-width/2),y,value,size,fill)
 def glyph(cx,cy,value,size,fill=INK):
  mask=font(size,size>=22).getmask(str(value),mode='L');alpha=Image.frombytes('L',mask.size,bytes(mask));bounds=alpha.getbbox()
  if bounds:
   alpha=alpha.crop(bounds);im.paste(fill,(round(cx-alpha.width/2),round(cy-alpha.height/2)),alpha)
 def rule(x1,y1,x2,y2,fill=LINE,width=1):d.line((x1,y1,x2,y2),fill=fill,width=width)
 def dot(x,y,fill,r=3):d.ellipse((x-r,y-r,x+r,y+r),fill=fill)
 def block(box,fill,r=9,outline=None):
  x0,y0,x1,y1=box
  d.rectangle((x0+r,y0,x1-r,y1),fill=fill);d.rectangle((x0,y0+r,x1,y1-r),fill=fill)
  for corner,angles in [((x0,y0,x0+2*r,y0+2*r),(180,270)),((x1-2*r,y0,x1,y0+2*r),(270,360)),((x0,y1-2*r,x0+2*r,y1),(90,180)),((x1-2*r,y1-2*r,x1,y1),(0,90))]:d.pieslice(corner,*angles,fill=fill)
 def fit_number(value,unit=''):
  if value is None:return '--'
  return ('%.2f'%value if value<1 else '%.1f'%value) + unit
 # Brand and folded-sheet mark.
 d.line([(23,23),(43,23),(56,36),(36,36),(23,23)],fill=INK,width=2);d.rectangle((36,36,56,54),outline=INK,width=2)
 rule(36,36,36,54,ORANGE,3);text(68,22,'FlexiFold',26);text(69,54,'双臂折叠 · FPGA 协同',16,MUTED)
 badge=ORANGE if state.get('is_synthetic') else INK
 block((357,24,456,54),badge,3);text(369,30,state['source_label'],18,INK if state.get('is_synthetic') else WHITE)
 rule(24,76,456,76,INK)
 # Main judgment. These words derive from packets and the PL, never a timed demo.
 tone=ORANGE if state['tone'] in ('waiting','alert','history','review') else LIME
 block((24,91,456,251),INK,10)
 # The chip identity is shown in the diagram; the judgment leads.
 headline_size=44
 while font(headline_size,True).getmask(state['headline'],mode='L').size[0]>296:headline_size-=1
 text(41,128,state['headline'],headline_size,WHITE,maxw=296)
 text(43,195,state['summary'],18,SHADE,maxw=366)
 dot(45,232,tone);status=state.get('activity_line') or ('任务数据实时更新' if state['fresh'] else '当前无新任务数据')
 if state.get('is_synthetic'):status='模拟联调 · 未运行机械臂'
 text(56,224,status,18,WHITE)
 # A drawn FPGA package, state gates light only with real current evidence.
 cx,cy=389,157
 for y in range(cy-30,cy+31,10):rule(cx-40,y,cx-31,y,GREEN,2);rule(cx+31,y,cx+40,y,GREEN,2)
 for x in range(cx-25,cx+26,10):rule(x,cy-41,x,cy-31,GREEN,2);rule(x,cy+31,x,cy+41,GREEN,2)
 d.rectangle((cx-30,cy-30,cx+30,cy+30),outline=LIME,width=2)
 for j in range(3):
  for i in range(3):
   enabled=state['phase_current'] and (i+j)<=state['phase']
   d.rectangle((cx-20+i*16,cy-20+j*16,cx-11+i*16,cy-11+j*16),fill=LIME if enabled else DARK)
 # Visible cooperation: three roles, with static role text and honest runtime states.
 text(24,269,state['cooperation']['summary'],18,maxw=432)
 labels=[('AI PC',state['cooperation']['host_status'],state['host_connected']),('FPGA',state['cooperation']['fpga_status'],state['config_matched']),(state['cooperation'].get('display_name','结果显示'),state['cooperation']['display_status'],True)]
 for index,(name,role,connected) in enumerate(labels):
  x=24+index*156
  block((x,298,x+120,364),WHITE,5)
  center(x+60,306,name,24,INK if connected else MUTED)
  center(x+60,337,role,20,MUTED)
  if index<2:
   mid=x+138;rule(x+126,323,x+146,323,INK,2);d.polygon([(mid+4,319),(mid+10,323),(mid+4,327)],fill=INK)
   if state['fresh']:dot(mid-5+(state.get('sequence') or 0)%3*3,323,ORANGE,3)
 # The web view explains claim provenance; keep the small screen uncluttered.
 # Task stage is the PL state machine, not the host's task claim.
 text(24,377,'FPGA 识别的动作阶段',18)
 phase_label=['等待','张爪','夹持','翻折'][min(state['phase'],3)]
 stage_note=('当前：' if state['phase_current'] else '上次：')+phase_label if state['phase'] else '等待识别'
 block((323,371,456,400),ORANGE if state['phase_current'] and state['phase'] else LINE,3)
 text(338,377,stage_note,18,INK)
 for i,label in enumerate(['张爪','夹持','翻折']):
  x=80+i*160;active=state['phase']>=i+1
  if i<2:rule(x+16,419,x+144,419,LIME if state['phase']>i+1 else LINE,4)
  current=state['phase']==i+1
  d.ellipse((x-14,405,x+14,433),fill=(ORANGE if state['phase_current'] else SHADE) if current else INK if active else LINE)
  if active and not current:d.line([(x-6,419),(x-1,424),(x+7,414)],fill=LIME,width=2)
  else:glyph(x,419,str(i+1),18,INK if current else MUTED)
  text(x-22 if current else x-19,439,label,22 if current else 18,INK if active else MUTED)
 # Constraint judgment and actual feedback age.
 block((24,477,302,567),WHITE,6);text(40,490,'动作数据检查',18,MUTED)
 color=RED if state['mask'] else INK
 judgment=state['authority_presentation']['reason_label'] if (state.get('authority') or {}).get('stage') in ('denied','dispatch_unknown','error') else state['violations'][0] if state['violations'] else ('上次未见异常' if state['run_samples'] and not state['fresh'] else state['guard_label'])
 text(40,522,judgment,28,color,maxw=246)
 block((314,477,456,567),WHITE,6);text(330,490,'发出时距更新',18,MUTED)
 age=state.get('feedback_age_us')
 value='--' if age is None else '%.0f'%(age/(1000000 if age>=1000000 else 1000))
 text(330,520,value,34)
 if age is not None:text(412,540,'s' if age>=1000000 else 'ms',18,MUTED)
 # Evidence and returned result, never conflated with a completed robot task.
 block((24,583,456,691),SAND,6)
 text(40,597,'任务结果与视觉证据',18)
 for i in range(3):
  x=348+i*31;lit=state['stable']>i
  block((x,596,x+24,620),INK if lit else SHADE,2)
  glyph(x+12,608,str(i+1),18,LIME if lit else MUTED)
 evidence_color=RED if state.get('task_result')=='failure' or (state['verify']==3 and not state.get('task_lifecycle')) else INK
 text(40,626,state['evidence_label'],28,evidence_color,maxw=290)
 frame=state.get('frame_id');event=state.get('event',0xffffffff)
 label=state['evidence_note']
 if state.get('is_synthetic'):label='模拟数据测试，非现场结果'
 text(40,663,label,18,MUTED,maxw=280)
 if state['verify']:text(342,638,'稳定 '+str(state['stable'])+'/'+str(state['min_frames']),18,maxw=110)
 # Only the essential numbers remain on the small panel.
 fields=[('本轮检查次数',str(state['run_samples']),''),('FPGA用时',fit_number(state['pl_compute_us']),'us'),('整板响应用时',fit_number(None if state['ps_service_us'] is None else state['ps_service_us']/1000),'ms')]
 for i,(name,value,unit) in enumerate(fields):
  x=24+i*151
  if i:rule(x-12,705,x-12,752,LINE)
  text(x,706,name,18,MUTED);text(x,730,value,30,maxw=110)
  if value!='--':text(x+111,741,unit,18,MUTED)
 rule(24,762,456,762,INK)
 text(24,773,'B35 · 50MHz · '+('配置匹配' if state['config_matched'] else '配置待核验'),14,MUTED)
 text(375,773,time.strftime('%H:%M:%S',time.localtime(state['board_epoch_s'])),14,MUTED)
 return im
