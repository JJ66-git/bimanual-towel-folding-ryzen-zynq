"""Shared rendering of observed state; no hardware access or action authority."""
import json
import time
from PIL import Image, ImageDraw, ImageFont

UNKNOWN = 0xffffffff
REASONS = ('TARGET RANGE', 'ACT RANGE', 'JOINT STEP', 'TRACKING', 'CORRECTION',
           'CORR SLEW', 'GRIP OWNER', 'START POSE', 'ACTION BUDGET', 'FEEDBACK AGE', 'PROFILE')


class ScreenState:
    def __init__(self):
        self.build_id = None
        self.check = None
        self.reply_sequence = None
        self.diag_session = None
        self.watchdog = None
        self.checked = self.flagged = self.rejected = 0
        self.stopped = False
        self.task = None
        self.last_received_ns = None

    def apply(self, row):
        event = row['event']
        if event == 'server_started':
            self.build_id = row['build_id']
        if event in ('check_reply_sent','action_observed','status','session_started',
                     'monitor_paused','monitor_resumed','session_ended','task_status_received'):
            self.last_received_ns = row.get('board_received_ns',row.get('board_monotonic_ns'))
        if event == 'task_status_received':
            self.task = {key:row[key] for key in ('phase','activity','origin','session','sequence')}
        if event == 'session_started':
            self.diag_session = row['session']
        if 'watchdog' in row:
            self.watchdog = row['watchdog'].copy()
        if event in ('check_reply_sent', 'action_observed'):
            if row['violations'] == UNKNOWN:
                raise ValueError('A completed check cannot have unknown violations')
            self.check = dict(sequence=row['sequence'], session=row['session'], mask=row['violations'],
                              namespace='FPG1' if event == 'check_reply_sent' else 'FWD1')
            self.checked += 1
            self.flagged += row['violations'] != 0
            self.reply_sequence = row['sequence'] if event == 'check_reply_sent' else None
        if event == 'diagnostic_reply_sent' and self.check is not None:
            if (self.check['namespace'] == 'FWD1' and self.check['session'] == self.diag_session
                    and self.check['sequence'] == row['sequence']):
                self.reply_sequence = row['sequence']
        if event == 'packet_rejected':
            self.rejected += 1
        if event == 'server_stopped':
            self.stopped = True

    def summary(self):
        mask = self.check['mask'] if self.check else None
        return dict(build_id=self.build_id, last_check=self.check, reply_sequence=self.reply_sequence,
                    checked=self.checked, flagged=self.flagged, rejected=self.rejected,
                    watchdog=self.watchdog, service_stopped=self.stopped,
                    reasons=[name for bit,name in enumerate(REASONS) if mask is not None and mask & (1 << bit)],
                    per_channel_results_available=False, task_status=self.task,
                    task_phase=self.task['phase'] if self.task else None)


def read_state(path):
    state = ScreenState()
    for number,line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(),1):
        if line.strip():
            try:
                state.apply(json.loads(line))
            except (ValueError,KeyError,TypeError) as error:
                raise ValueError('Invalid event on line {}: {}'.format(number,error)) from error
    return state


def render(state, mode, input_source):
    if mode not in ('LIVE','REPLAY'):
        raise ValueError('invalid display mode')
    image = Image.new('RGB',(480,800),(5,17,29))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    white,cyan,grey = (236,245,255),(28,211,235),(118,142,163)

    def text(position,value,color=white,scale=3,max_width=440):
        mask = font.getmask(str(value))
        if not mask.size[0] or not mask.size[1]:
            return
        alpha = Image.frombytes('L',mask.size,bytes(mask))
        width,height = alpha.width*scale,alpha.height*scale
        if width > max_width:
            height=max(1,int(height*max_width/width))
            width=max_width
        image.paste(color,position,alpha.resize((width,height),Image.NEAREST))

    def panel(box,fill=(9,30,47),outline=cyan):
        draw.rectangle(box,fill=fill,outline=outline,width=2)

    text((22,23),'FPGA COOP MONITOR',scale=4)
    source={'synthetic_test':'SYNTHETIC','recorded_replay':'RECORDED',
            'host_observation':'HOST CLAIMED'}.get(input_source,'SOURCE UNKNOWN')
    text((23,70),mode+' | '+source+' | SHADOW',cyan,scale=2)
    panel((18,103,462,148),outline=(54,82,105))
    task=state.task
    title=(task['origin']+': '+task['phase']+' / '+task['activity']) if task else 'TASK PHASE -- (HOST EVENT REQUIRED)'
    text((31,116),title,grey,scale=2,max_width=418)
    seq=str(state.check['sequence']) if state.check else '--'
    for x,title,value in [(18,'AI PC','SEQ '+seq),(173,'PS + PL','14-DIM CHECK'),
                           (328,'RESULT','SEQ '+str(state.reply_sequence) if state.reply_sequence is not None else 'WAIT REPLY')]:
        panel((x,172,x+134,244))
        text((x+12,187),title,scale=3,max_width=112)
        text((x+12,219),value,cyan,scale=2,max_width=112)
    for x in (153,308):
        draw.polygon(((x,203),(x+16,208),(x,213)),fill=cyan)
    mask=state.check['mask'] if state.check else None
    color=(37,230,167) if mask==0 else (255,105,85) if mask is not None else grey
    panel((18,266,462,367),fill=(9,37,43),outline=color)
    text((32,279),'LAST PL CHECK - '+('HISTORY' if mode=='REPLAY' else 'SNAPSHOT'),grey,scale=2)
    text((32,310),'NO VIOLATIONS' if mask==0 else 'VIOLATIONS' if mask is not None else 'NO RESULT',color,scale=4)
    text((25,383),'MASK '+('0x{:08X}'.format(mask) if mask is not None else '--'),grey,scale=2)
    if state.summary()['reasons']:
        text((220,383),state.summary()['reasons'][0],color,scale=2,max_width=235)
    panel((18,415,462,513),outline=(63,96,119))
    watchdog=state.watchdog
    names={0:'WAIT BEGIN',1:'MONITORING',2:'PAUSED',3:'ON RESULT',4:'ENDED'}
    status=watchdog['status'] if watchdog else None
    wd='SERVICE STOPPED' if state.stopped else names.get(watchdog['state'],'UNKNOWN') if watchdog else 'UNKNOWN'
    if watchdog and not state.stopped and watchdog['state']==1:
        wd='ACTION GAP' if status & 4 else 'MONITORING' if status & 2 else 'WAIT FIRST ACTION'
    text((32,429),'WATCHDOG',cyan,scale=2)
    text((32,458),wd,(255,105,85) if wd=='ACTION GAP' else white,scale=3)
    age='{:.1f} ms'.format(watchdog['age_cycles']/50000) if watchdog else '--'
    received='{:.1f}s'.format(max(0,time.monotonic_ns()-state.last_received_ns)/1e9) if mode=='LIVE' and state.last_received_ns is not None else '--'
    text((32,492),'PL AGE '+age+' / RX '+received,grey,scale=2,max_width=414)
    text((24,539),'14 VALUES / NO CHANNEL MASK',grey,scale=2)
    for y,label in [(583,'LEFT'),(615,'RIGHT')]:
        text((24,y-5),label,grey,scale=2,max_width=70)
        for channel in range(7):
            x=118+47*channel
            draw.rectangle((x,y,x+20,y+20),fill=(40,53,65),outline=grey)
    text((25,650),'DETAIL UNKNOWN - NOT GREEN PASS',grey,scale=2)
    timeout=str(watchdog['timeout_count']) if watchdog else '--'
    for x,y,title,value in [(24,690,'CHECKS (PS)',state.checked),(255,690,'REJECTED (PS)',state.rejected),
                            (24,738,'TIMEOUTS (PL)',timeout),(255,738,'BUILD',hex(state.build_id) if state.build_id else '--')]:
        text((x,y),title,grey,scale=2,max_width=195)
        text((x,y+21),value,cyan,scale=2,max_width=195)
    text((25,782),mode+' / LAST RESULT IS NOT MOTION AUTHORIZATION',grey,scale=1)
    return image
