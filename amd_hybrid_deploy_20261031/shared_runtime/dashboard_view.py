"""Display-only FlexiFold scene. Pillow text/images; native Tk Canvas charts.

The same layout and drawing functions export offline screenshots. No control,
model inference, camera opening, CAN or FPGA network calls live in this module.
"""
from collections import deque
from functools import lru_cache
import math
from pathlib import Path
import time

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

BG = '#F3F2EC'
PANEL = '#E9ECE2'
INK = '#18241E'
MUTED = '#58665A'
LINE = '#CDD3C6'
ACCENT = '#C4E78A'
GREEN = '#38633A'
WARN = '#85501D'
RED = '#A33832'
DARK = '#151D19'
LIGHT = '#E4EADD'
TERMINAL = ('finished', 'stopped', 'error')
STALE_S = 1.5


@lru_cache(maxsize=128)
def font(size, bold=False, mono=False):
    if mono:
        path = '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'
    else:
        path = '/usr/share/fonts/opentype/noto/NotoSansCJK-'+('Bold' if bold else 'Regular')+'.ttc'
    return ImageFont.truetype(path, size, index=2 if not mono else 0)


class DashboardState:
    def __init__(self, mode='LIVE'):
        self.mode = mode
        self.images = {}
        self.camera_times = {}
        self.joints = None
        self.poses = {}
        self.feedback_times = {}
        self.control = None
        self.board = None
        self.board_history = ()
        self.board_link = None
        self.authority = None
        self.history = deque(maxlen=50)
        self._last_stamp = None

    def ingest(self, packet):
        self.images.update(packet.get('images', {}))
        for role in packet.get('images', {}):
            self.camera_times[role] = packet['sampled']
        self.joints = packet.get('joints') or self.joints
        self.poses = packet.get('poses', {})
        self.feedback_times = packet.get('feedback_times', {})
        value = packet.get('control')
        if value is not None:
            if self.control is None or value.run_id != self.control.run_id:
                self.history.clear()
                self._last_stamp = None
                self.board = None
                self.board_history = ()
                self.board_link = None
                self.authority = None
                self.control = None
            if self._last_stamp is None or value.sampled_mono_s > self._last_stamp:
                self.control = value
                # A terminal timestamp is a status update, not a new control sample.
                if value.run_state == 'running' and value.authority is not None:
                    self.history.append((value.sampled_mono_s, value.authority))
                self._last_stamp = value.sampled_mono_s
        if packet.get('board_link') is not None:
            link=packet['board_link']
            if self.board_link is not None and self.board_link.session!=link.session:
                self.authority=None;self.board=None;self.board_history=()
            self.board_link=link
        authority=packet.get('authority')
        if authority is not None and self.control is not None and (self.board_link is None or self.board_link.session==authority.session):
            prior=self.authority
            if prior is None or authority.session!=prior.session or authority.sampled_mono_s>prior.sampled_mono_s and authority.values['target_sequence']>=prior.values['target_sequence']:
                self.authority=authority
        last_step=self.control.step if self.control is not None else -1
        if self.authority is not None and self.authority.values['step']!=0xffffffff:last_step=max(last_step,self.authority.values['step'])
        board = packet.get('board')
        if board is not None and self.control is not None and 0 <= board.step <= last_step:
            self.board = board
            self.board_history = tuple(item for item in packet.get('board_history',()) if item.session==board.session and item.step<=last_step)[-32:]


@lru_cache(maxsize=1)
def authority_presenter():
    import importlib.util
    path=Path(__file__).resolve().parents[2]/'fpga_integration/fpga_code/fpga_workspace/host/action_status.py'
    spec=importlib.util.spec_from_file_location('shared_authority_presentation',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module.presentation


def authority_projection(state, now=None):
    value=state.authority
    if value is None:return None
    now=time.monotonic() if now is None else now
    row=value.values
    fresh=now-value.sampled_mono_s<=STALE_S
    if row['stage']=='allowed':fresh=fresh and now-value.sampled_mono_s<row['budget_us']/1e6
    return authority_presenter()(row,fresh=fresh)


def layout(width, height, details=False):
    compact = width < 1120
    pad = 24 if width >= 1120 else 16
    gap = 20 if width >= 1120 else 14
    sidebar = 316 if width < 1500 else 348
    # The header subtitle ends near y=87 at the shipped font size. Keep
    # the divider below it at every height; compact windows must not compress it.
    top = 120
    bottom = height-94
    if details:
        bottom -= 182
    sidebar_x = width-pad-sidebar
    thumb_w = max(200, int((width-2*pad)*.205)) if not compact else 0
    center_x = pad+thumb_w+gap if not compact else pad
    center_right = sidebar_x-gap
    return dict(pad=pad, gap=gap, top=top, bottom=bottom, sidebar_x=sidebar_x,
        sidebar_w=sidebar, center=(center_x,top,center_right,bottom),
        thumbs=None if compact else (pad,top,pad+thumb_w,bottom),
        chart=height >= 730 and width >= 960,
        details_button=(width-pad-126,26,width-pad,66),
        tabs=(max(390,width/2-120),26))


def chart_geometry(state, rect, now):
    x,y,r,b = rect
    terminal = state.control is not None and state.control.run_state in TERMINAL
    end = state.history[-1][0] if terminal and state.history else now
    segments, current = [], []
    last = None
    for stamp, value in state.history:
        if not math.isfinite(value) or not end-10 <= stamp <= end:
            continue
        if last is not None and stamp-last > STALE_S:
            if current: segments.append(current)
            current = []
        current.append((x+(stamp-(end-10))/10*(r-x), b-value*(b-y)))
        last = stamp
    if current: segments.append(current)
    return segments


def render_dashboard(state, width, height, now=None, details=False, draw_chart=True, page='cameras', frozen=False, fpga_scroll=0):
    now = time.monotonic() if now is None else now
    image = Image.new('RGB',(width,height),BG)
    draw = ImageDraw.Draw(image)
    geo = layout(width,height,details and page=='cameras')
    p,top,bottom,sx,sw = (geo[k] for k in ('pad','top','bottom','sidebar_x','sidebar_w'))
    control = state.control
    terminal = control is not None and control.run_state in TERMINAL
    stale = control is not None and not terminal and now-control.sampled_mono_s > STALE_S
    state_label = {'preparing':'准备中','running':'执行中','finished':'本轮结束',
                   'stopped':'已停止','error':'任务出错'}.get(getattr(control,'run_state',None),'等待任务')
    state_color = RED if control and control.run_state == 'error' else WARN if stale else GREEN

    def text(x,y,value,size=14,fill=INK,bold=False,mono=False,anchor=None):
        draw.text((x,y),str(value),font=font(size,bold,mono),fill=fill,anchor=anchor)

    def rule(y,x=p,r=None):
        draw.line((x,y,width-p if r is None else r,y),fill=LINE,width=1)

    def chip(x,y,value,color=GREEN,background=ACCENT):
        box=draw.textbbox((0,0),value,font=font(12,True))
        w=box[2]+22
        draw.rounded_rectangle((x,y,x+w,y+28),radius=6,fill=background)
        text(x+11,y+4,value,12,color,True)
        return w

    # A compact crease mark, drawn as geometry, not a decorative illustration.
    draw.line((p,35,p+16,35,p+24,46,p+8,46,p,35),fill=INK,width=2)
    draw.line((p+8,46,p+8,57,p+24,57,p+24,46),fill=INK,width=2)
    text(p+38,20,'FlexiFold',30,bold=True)
    text(p+40,62,'双臂折叠 · 实时观察',13,MUTED)
    mode = state.mode
    if page=='fpga' and frozen:mode='FROZEN'
    if mode == 'LIVE' and control is not None and not control.execute:
        mode = 'READ ONLY'
    if mode == 'LIVE' and not state.images and control is None:
        mode = 'WAITING'
    if width >= 1120:
        chip(p+255,32,mode, WARN if mode in ('SIMULATION','REPLAY','FROZEN') else GREEN,
             '#EFE2CC' if mode in ('SIMULATION','REPLAY','FROZEN') else '#E1E9D7')
        text(p+255,65,'画面与控制分别标时',12,MUTED)
    else:
        chip(p+240,34,mode,WARN if mode in ('SIMULATION','REPLAY','FROZEN') else GREEN,
             '#EFE2CC' if mode in ('SIMULATION','REPLAY','FROZEN') else '#E1E9D7')
    if width >= 1280:
        text(sx-135,30,state_label,15,state_color,True)
        text(sx-135,55,'仅供观察',12,MUTED)
    nav_x,nav_y=geo['tabs']
    for i,(key,label) in enumerate((('cameras','实时画面'),('fpga','FPGA 协同'))):
        nx=nav_x+i*112
        draw.rounded_rectangle((nx,nav_y,nx+104,nav_y+40),radius=9,fill=ACCENT if page==key else BG)
        text(nx+52,nav_y+9,label,14,INK if page==key else MUTED,bold=page==key,anchor='ma')
    bx,by,br,bb=geo['details_button']
    if page=='cameras':
        draw.rounded_rectangle((bx,by,br,bb),radius=9,outline=LINE,fill=PANEL)
        text(bx+18,by+9,'收起参数' if details else '参数详情',14,bold=True)
    else:chip(bx+20,by+6,'状态与回执',GREEN,PANEL)
    rule(top-18)

    def camera(role, rect, caption, main=False):
        x,y,r,b=rect
        text(x,y-1,caption,16 if main else 13,bold=True)
        stamp=state.camera_times.get(role)
        age=None if stamp is None else max(0.,now-stamp)
        fresh=age is not None and age <= STALE_S
        status='等待画面' if age is None else f'{age:.1f}s'+(' · 过期' if not fresh else '')
        text(r,y+2,status,12,GREEN if fresh else WARN,mono=False,anchor='ra')
        view=(x,y+35,r,b-26)
        vx,vy,vr,vb=view
        draw.rounded_rectangle(view,radius=10,fill=DARK)
        frame=state.images.get(role)
        if frame is not None:
            frame=Image.fromarray(frame)
            scale=min((vr-vx-2)/frame.width,(vb-vy-2)/frame.height)
            frame=frame.resize((max(1,round(frame.width*scale)),max(1,round(frame.height*scale))),Image.Resampling.BILINEAR)
            if not fresh:
                frame=ImageEnhance.Color(frame).enhance(0.)
                frame=ImageEnhance.Brightness(frame).enhance(.45)
            at=(int(vx+(vr-vx-frame.width)/2),int(vy+(vb-vy-frame.height)/2))
            image.paste(frame,at)
            ImageDraw.Draw(image,'RGBA').rectangle((at[0],at[1],at[0]+frame.width-1,at[1]+frame.height-1),outline=(255,255,255,26),width=1)
            if not fresh:
                label='画面已过期'
                draw.rounded_rectangle((vx+12,vy+12,vx+122,vy+44),radius=6,fill=DARK)
                text(vx+22,vy+18,label,14,'#E8C68D')
        else:
            cx,cy=(vx+vr)/2,(vy+vb)/2
            draw.rounded_rectangle((cx-17,cy-32,cx+17,cy-8),radius=4,outline='#7D8B7E',width=2)
            draw.line((cx+17,cy-26,cx+26,cy-30,cx+26,cy-10,cx+17,cy-14),fill='#7D8B7E',width=2)
            text(cx,cy+8,'等待相机缓存',15,LIGHT,anchor='ma')
            if main:text(cx,cy+40,'相机连接后自动显示完整画幅',12,'#A6B2A5',anchor='ma')
        text(x,b-18,'640 × 480 RGB' if frame is not None and frame.size == (640,480) else 'RGB · 保持原始比例',11,MUTED)
        if main:text(r,b-18,'中央主画面',11,MUTED,anchor='ra')

    if page=='cameras':
        if geo['thumbs']:
            x,y,r,b=geo['thumbs']; mid=(y+b-16)/2
            camera('left',(x,y,r,mid),'左视角 / LEFT')
            camera('right',(x,mid+20,r,b),'右视角 / RIGHT')
        camera('middle',geo['center'],'中央视角 / MIDDLE',True)

        # One continuous telemetry region, with separators instead of nested cards.
        sidebar_bottom=height-82
        draw.rounded_rectangle((sx,top,sx+sw,sidebar_bottom),radius=12,fill=PANEL)
        tx=sx+22; tr=sx+sw-22; ty=top+18
        ap=authority_projection(state,now)
        if ap:
            text(tx,ty,ap['headline'],20,RED if ap['tone']=='alert' else GREEN if ap['tone']=='active' else WARN,True)
            text(tx,ty+32,ap['mode_label']+' · '+ap['sink_label'],12,MUTED)
            text(tx,ty+53,ap['reason_label'] if ap['tone']=='alert' else ap['outcome_label'],12,MUTED)
            ty+=82
        text(tx,ty,'SmolVLA 修正',18,bold=True)
        text(tx,ty+31,'当前融合权重',13,MUTED)
        valid=control is not None and control.authority is not None
        percent='--' if not valid else f'{control.authority*100:.1f}'
        color=MUTED if stale or not valid else INK
        text(tx,ty+50,percent,62,fill=color,mono=True)
        if valid:text(tr,ty+87,'%',24,MUTED,anchor='ra')
        bar=(tx,ty+141,tr,ty+149)
        draw.rounded_rectangle(bar,radius=4,fill='#C9D1C2')
        geo['authority_bar']=bar
        geo['authority_value']=control.authority if valid else None
        geo['authority_stale']=stale
        if draw_chart and valid and control.authority>0:
            draw.rounded_rectangle((tx,bar[1],tx+(tr-tx)*control.authority,bar[3]),radius=4,fill='#71806D' if stale else GREEN)
        text(tx,ty+158,'0%',11,MUTED,mono=True)
        text(tr,ty+158,'100%',11,MUTED,mono=True,anchor='ra')
        cursor=ty+187
        subtitle = '等待控制遥测' if not control else ('本轮结束 · 保留最后读数' if terminal else
            f'数据已过期 · {max(0.,now-control.sampled_mono_s):.1f}s' if stale else
            f'控制步 {control.step:04d} · '+('已下发' if control.execute else '仅候选'))
        text(tx,cursor,subtitle,12,WARN if stale else MUTED)
        cursor+=33
        chart_rect=None
        if geo['chart']:
            text(tx,cursor,'最近 10 秒',13,bold=True)
            text(tr,cursor,'0–100%',11,MUTED,anchor='ra')
            chart_rect=(tx,cursor+32,tr,cursor+138)
            for value in (0,.5,1):
                yy=chart_rect[3]-value*(chart_rect[3]-chart_rect[1])
                draw.line((tx,yy,tr,yy),fill=LINE)
            if draw_chart:
                draw_graph(draw,chart_geometry(state,chart_rect,now),chart_rect,stale)
            if not state.history:text((tx+tr)/2,cursor+71,'等待权重采样',12,MUTED,anchor='ma')
            text(tx,cursor+148,'−10s',11,MUTED,mono=True)
            text(tr,cursor+148,'当前',11,MUTED,anchor='ra')
            cursor+=181
        draw.line((tx,cursor,tr,cursor),fill=LINE)
        cursor+=15
        text(tx,cursor,'累计修正步数占比',13,MUTED)
        sent=control.dispatched_steps if control else 0
        corrected=control.corrected_steps if control else 0
        ratio=f'{corrected/sent*100:.1f}%' if control and control.execute and sent else '--'
        text(tx,cursor+24,ratio,30,bold=True,mono=True)
        detail=f'{corrected} / {sent} 步' if control and control.execute else '只读：不累计实际下发' if control else '等待实际下发'
        text(tr,cursor+38,detail,11,MUTED,anchor='ra')
        cursor+=78
        draw.line((tx,cursor,tr,cursor),fill=LINE)
        cursor+=13
        if cursor+68<sidebar_bottom:
            text(tx,cursor,'当前最大关节修正',13,MUTED)
            delta=getattr(control,'max_correction_rad',None)
            text(tx,cursor+26,'--' if delta is None else f'{delta:.3f}',28,bold=True,mono=True)
            text(tr,cursor+40,'rad',13,MUTED,mono=True,anchor='ra')
            cursor+=78
        if cursor+43 < sidebar_bottom:
            text(tx,cursor,'ACT 提供基础动作与夹爪',12,MUTED)
            text(tx,cursor+22,'SmolVLA 对手臂做有界修正',12,MUTED)
        if control and control.max_authority is not None and cursor+75 < sidebar_bottom:
            text(tx,cursor+48,f'本轮权重上限 {control.max_authority*100:.1f}%',11,MUTED)

    else:
        render_fpga(draw,state,geo,width,height,now,frozen,fpga_scroll)
        chart_rect=None

    # Compact gripper and matching-board information stay outside camera pixels.
    fy=height-65
    rule(fy-12)
    text(p,fy,state_label+(' · 数据过期' if stale else ''),14,state_color,True)
    elapsed=getattr(control,'elapsed_s',0.)
    elapsed_text=f'{int(elapsed)//60:02d}:{int(elapsed)%60:02d}' if control else '--:--'
    step_text=f'{control.step+1} / {control.max_actions}' if control and control.step>=0 else '-- / --'
    text(p+170,fy,step_text+' 步',13,mono=False)
    text(p+310,fy,elapsed_text,14,mono=True)
    joint=state.joints
    fresh_joints=joint is not None and now-joint['timestamp']<=STALE_S and all(
        state.feedback_times.get(side,{}).get('gripper') is not None and
        -2<=time.time()-state.feedback_times[side]['gripper']<=STALE_S for side in ('left','right'))
    grips='夹爪 L -- / R -- mm' if joint is None else f'夹爪 L {joint["left_gripper_mm"]:.1f} / R {joint["right_gripper_mm"]:.1f} mm'
    if width>=1120:text(p+425,fy,grips,13,MUTED if fresh_joints else WARN)
    board=state.board
    ap=authority_projection(state,now)
    if ap:
        board_text=ap['headline']+' · #'+str(state.authority.values['target_sequence'])
        board_color=RED if ap['tone']=='alert' else GREEN if ap['tone']=='active' else WARN
    elif board:
        fresh=terminal or now-board.sampled_mono_s<=STALE_S
        board_text=f'PL 检查 #{board.sequence} · 步 {board.step}'
        verdict='未见违规' if board.reference_match and not board.violations else '需复核'
        board_color=GREEN if fresh and verdict=='未见违规' else WARN
        board_text+=' · '+(verdict if fresh else '过期')
    else:board_text='PL 检查 -- · 等待匹配回包';board_color=MUTED
    text(width-p,fy,board_text,12,board_color,anchor='ra')
    note='SIMULATION · 模拟画面与遥测，不连接硬件' if state.mode=='SIMULATION' else '关闭窗口只关闭展示；任务控制仍由运行终端负责'
    text(p,height-29,note,11,MUTED)
    if width>=1120:
        vision_text='视觉复核：尚未接入'
        if board is not None and board.protocol_version==3:
            names=('等待撤臂','跨帧检查','视觉状态已返回','未确认')
            vision_text='视觉复核：'+(names[board.verify_state] if board.verify_state is not None else '等待证据')
            if board.calibration_status!='field_validated':vision_text+=' · 未现场标定'
        text(width-p,height-29,vision_text,11,MUTED,anchor='ra')

    if details and page=='cameras':
        dy=bottom+15
        rule(dy,r=geo['center'][2])
        for col,side in enumerate(('left','right')):
            dx=p+col*(sx-geo['gap']-p)/2
            text(dx,dy+9,'左臂' if side=='left' else '右臂',14,bold=True)
            values=joint[side+'_joints'] if joint else None
            for row in range(2):
                label='等待关节反馈' if values is None else '  '.join(f'J{i+1} {values[i]:+.3f}' for i in range(row*3,row*3+3))
                text(dx,dy+36+row*22,label,12,MUTED,mono=values is not None)
            pose=state.poses.get(side)
            fresh_pose=pose is not None and -2<=time.time()-pose['timestamp']<=STALE_S
            if pose:
                text(dx,dy+83,'XYZ mm  '+'  '.join(f'{v:+.1f}' for v in pose['xyz_mm']),12,MUTED)
                text(dx,dy+107,'RX RY RZ °  '+'  '.join(f'{v:+.1f}' for v in pose['rotation_deg']),12,MUTED)
            else:text(dx,dy+83,'末端位姿：暂无有效 SDK 反馈',12,WARN)
            text(dx,dy+134,'SDK 缓存 · '+('反馈正常' if fresh_pose else '反馈过期或未知'),11,MUTED)
        if control:
            selected=f'{control.candidate_steps} / {control.dispatched_steps} 步' if control.execute else '--（只读）'
            text(p,dy+156,'候选采用：'+selected+' · 当前来源 '+(control.source or '--'),10,MUTED)
    return image, geo, chart_rect


def draw_graph(draw,segments,rect,stale=False):
    color=MUTED if stale else GREEN
    for points in segments:
        if len(points)>1:draw.line(points,fill=color,width=2)
        elif points:
            x,y=points[0];draw.ellipse((x-2,y-2,x+2,y+2),fill=color)
    if segments and segments[-1]:
        x,y=segments[-1][-1];draw.ellipse((x-3,y-3,x+3,y+3),fill=color)


VIOLATION_LABELS=('目标越界','ACT 越界','关节步长','跟踪偏差','修正幅度','修正变化率',
                  '夹爪归属','起点偏差','动作预算','反馈过期','配置不匹配')


def violation_labels(mask):
    labels=[name for bit,name in enumerate(VIOLATION_LABELS) if mask & (1<<bit)]
    if mask >> len(VIOLATION_LABELS):labels.append('未知违规位')
    return labels


def render_fpga(draw,state,geo,width,height,now,frozen,scroll):
    p,top,sx,sw=(geo[k] for k in ('pad','top','sidebar_x','sidebar_w'))
    right=sx-geo['gap'];bottom=height-82;main_w=right-p
    board=state.board;link=state.board_link;control=state.control
    stale=board is not None and not frozen and (control is None or control.run_state not in TERMINAL) and now-board.sampled_mono_s>STALE_S
    good=board is not None and board.reference_match and not board.violations
    color=WARN if stale else GREEN if good else RED if board else MUTED
    def text(x,y,value,size=14,fill=INK,bold=False,mono=False,anchor=None):
        draw.text((x,y),str(value),font=font(size,bold,mono),fill=fill,anchor=anchor)
    text(p,top,'PC ↔ FPGA',25,bold=True)
    ap=authority_projection(state,now);ar=state.authority.values if ap else None
    text(p,top+40,(ap['mode_label']+' · '+ap['sink_label']) if ap else '同一会话的控制步与 PL 回包对照',12,MUTED)
    action_y=top+4 if main_w>=720 else top+66
    for i,label in enumerate(('继续实时' if frozen else '冻结显示','复制诊断')):
        x=right-224+i*116
        draw.rounded_rectangle((x,action_y,x+108,action_y+40),radius=8,
            fill=ACCENT if frozen and i==0 else PANEL)
        text(x+54,action_y+10,label,13,bold=True,anchor='ma')
    if frozen:text(p,top+62,'显示已冻结 · 任务与回包仍继续',12,WARN)
    flow_y=top+91 if main_w>=720 else top+121
    draw.rounded_rectangle((p,flow_y,right,flow_y+108),radius=12,fill=PANEL)
    dispatched=control is not None and control.execute and control.dispatched_steps>0
    stages=(('策略下发',f'{control.dispatched_steps} 个控制步' if dispatched else '等待实际下发',dispatched),
            ('FPG1 采样','同一任务会话' if board else '等待回包证据',board is not None),
            ('PS / PL 检查','已收到 PL 结果' if board else '尚无匹配回包',board is not None),
            ('参考对照','一致' if board and board.reference_match else '不一致' if board else '等待对照',board is not None and board.reference_match))
    if ap:
        stages=(('候选数据',ap['host_status'],True),('PL 判定',ap['fpga_status'],ar['mask']!=0xffffffff),
                ('主机回执',ap['dispatch_status'],ar['outcome']=='sent'),('结果来源',ap['sink_label'],True))
    cell=main_w/4
    for i,(label,detail,active) in enumerate(stages):
        x=p+i*cell
        if i:draw.line((x,flow_y+20,x,flow_y+89),fill=LINE)
        draw.ellipse((x+14,flow_y+15,x+20,flow_y+21),fill=RED if i==3 and board is not None and not board.reference_match else GREEN if active else '#9BA696')
        text(x+14,flow_y+33,label,13,bold=True)
        text(x+14,flow_y+68,detail,10 if main_w<720 else 12,MUTED)
    table_y=flow_y+137
    text(p,table_y,'最近匹配检查',16,bold=True)
    text(right,table_y+3,'滚轮查看 · 最多 32 笔',11,MUTED,anchor='ra')
    head_y=table_y+40
    wide=main_w>=720
    columns=(('序号',0),('控制步',.13),('参考对照',.28),('违规位',.49),('往返 ms',.65),('板端 ms',.82)) if wide else (
        ('序号',0),('控制步',.18),('结果',.38),('往返 ms',.73))
    for label,fraction in columns:text(p+fraction*main_w,head_y,label,11,MUTED)
    draw.line((p,head_y+25,right,head_y+25),fill=LINE)
    history=list(reversed(state.board_history))
    if board is not None and not history:history=[board]
    offset=min(max(0,scroll),max(0,len(history)-1))
    rows=max(1,int((bottom-head_y-57)/31))
    shown=history[offset:offset+rows]
    for i,value in enumerate(shown):
        y=head_y+36+i*31
        mismatch=not value.reference_match or bool(value.violations)
        result='参考不一致' if not value.reference_match else '检测到违规' if value.violations else '未见违规'
        values=(f'#{value.sequence}',f'{value.step:04d}','一致' if value.reference_match else '不一致',
                f'0x{value.violations:03X}',f'{value.round_trip_ms:.2f}',f'{value.board_processing_ms:.3f}') if wide else (
                f'#{value.sequence}',f'{value.step:04d}',result,f'{value.round_trip_ms:.2f}')
        for j,((_,fraction),value_text) in enumerate(zip(columns,values)):
            text(p+fraction*main_w,y,value_text,11,RED if mismatch else INK,mono=j in (0,1) or (wide and j>=3) or (not wide and j==3))
        draw.line((p,y+23,right,y+23),fill=LINE)
    if not history:
        text(p+main_w/2,head_y+71,'等待真实 FPG1 任务回包',16,MUTED,anchor='ma')
        text(p+main_w/2,head_y+105,'启动探针、FUI1 状态确认不计入这里',11,MUTED,anchor='ma')
    text(p,bottom-20,('冻结快照 · ' if frozen else '')+f'已显示 {len(shown)} / {len(history)} 笔 · 含本轮核准候选的匹配回包',11,MUTED)

    draw.rounded_rectangle((sx,top,sx+sw,bottom),radius=12,fill=PANEL)
    tx=sx+22;tr=sx+sw-22;ty=top+18
    text(tx,ty,'逐条动作核准' if ap else 'PL 检查',18,bold=True)
    headline=ap['headline'] if ap else '等待回包' if board is None else '回包已过期' if stale else '未见违规' if good else '需复核'
    if ap:color=RED if ap['tone']=='alert' else GREEN if ap['tone']=='active' else WARN
    size=28
    while font(size,True).getlength(headline)>tr-tx and size>16:size-=1
    text(tx,ty+35,headline,size,color,True)
    detail=ap['reason_label'] if ap and ap['tone']=='alert' else ap['outcome_label'] if ap else '等待同会话任务回包' if board is None else ('整数参考一致' if board.reference_match else '整数参考不一致')
    text(tx,ty+82,detail,12,MUTED)
    text(tx,ty+118,'往返 / ms',11,MUTED)
    text(tx+(tr-tx)/2+8,ty+118,'下发时限 / ms' if ap else '板端 / ms',11,MUTED)
    current=board is not None and (not ap or board.session==state.authority.session and board.sequence==ar['target_sequence'])
    rtt=(ar['round_trip_us']/1000 if ar['round_trip_us']!=0xffffffff else None) if ap else board.round_trip_ms if board else None
    text(tx,ty+139,'--' if rtt is None else f'{rtt:.2f}',24,mono=True)
    metric=f"{ar['budget_us']/1000:.0f}" if ap else '--' if board is None else f'{board.board_processing_ms:.3f}'
    text(tx+(tr-tx)/2+8,ty+139,metric,24,mono=True)
    draw.line((tx,ty+180,tr,ty+180),fill=LINE)
    session=state.authority.session if ap else board.session if board else link.session if link else None
    def number(value):return '--' if value==0xffffffff else str(value)
    info=(('会话','--' if session is None else f'{session:08X}'),
          ('当前动作序号',str(ar['target_sequence']) if ap else '--' if board is None else f'#{board.sequence}'),
          ('对应控制步',number(ar['step']) if ap else '--' if board is None else str(board.step)))
    if ap:
        age='未知' if ar['feedback_age_us']==0xffffffff else f"{ar['feedback_age_us']/1000:.1f} ms"
        info+=(('运行方式',ap['mode_label']),('接收端',ap['sink_label']),('下发回执',ap['outcome_label']),
               ('发送时反馈年龄',age),('累计接收数',str(ar['sent_count'])),
               ('本条 PL 违规位','--' if not current or ar['mask']==0xffffffff else f"0x{ar['mask']:03X}"))
    else:
        info+=(('参考一致','--' if board is None else str(board.reference_matches)),
               ('参考不一致','--' if board is None else str(board.reference_mismatches)),
               ('含违规的回包','--' if board is None else str(board.violation_count)))
    if board is not None and board.protocol_version==3:
        info+=(('协议 / PL',f'v3 / {board.build_id:08X}' if board.build_id is not None else 'v3 / --'),
               ('PL 动作阶段','--' if board.phase_state is None else ('WAIT','OPEN','GRIP','FOLD')[board.phase_state]),
               ('视觉复核','--' if board.verify_state is None else ('WAIT','CHECKING','FOLD_OBSERVED','UNCONFIRMED')[board.verify_state]))
    if link:info+=(('PC / PL 步数预算',f'{link.controller_budget} / {link.fpga_budget}'),('配置 CRC',link.profile_crc))
    for i,(label,value) in enumerate(info):
        y=ty+197+i*29
        if y+20<bottom-65:
            text(tx,y,label,12,MUTED);text(tr,y,value,12,INK,anchor='ra')
    if ap:
        text(tx,bottom-53,'测试回执不代表机械臂执行' if ar['sink']=='memory' else '下发回执不代表机械臂执行',11,MUTED)
        text(tx,bottom-29,'折叠结果需独立确认',11,MUTED)
    else:text(tx,bottom-29,'旁路观测 · 不参与动作放行',11,MUTED)


def freeze_view(state):
    import copy
    value=copy.copy(state)
    value.images=state.images.copy()
    value.history=deque(state.history,maxlen=50)
    return value


def diagnostic_payload(state, frozen=False):
    from dataclasses import asdict
    return dict(schema='flexifold-display-diagnostic/1',display_mode=state.mode,
        display_frozen=bool(frozen),copied_mono_s=time.monotonic(),
        control=asdict(state.control) if state.control else None,
        board=asdict(state.board) if state.board else None,
        board_link=asdict(state.board_link) if state.board_link else None,
        authority=asdict(state.authority) if state.authority else None,
        recent_checks=[asdict(item) for item in state.board_history],
        decoded_violations=violation_labels(state.board.violations) if state.board else [],
        scope='Correlated FPG1 candidate checks and HOST display receipts; a display receipt does not certify robot execution or folding success.')


def window(frames,stopping,ready,title,mode='LIVE',initial_page='cameras'):
    from .tk_support import load_tk
    tk=load_tk()
    from PIL import ImageTk
    root=tk.Tk()
    root.title(title)
    root.configure(bg=BG)
    root.minsize(860,620)
    root.geometry(f'{min(1520,root.winfo_screenwidth())}x{min(940,root.winfo_screenheight())}')
    canvas=tk.Canvas(root,bg=BG,highlightthickness=0)
    canvas.pack(fill='both',expand=True)
    state=DashboardState(mode)
    details=False;page=initial_page;frozen_view=None;frozen_at=None;fpga_scroll=0;copied_until=0.
    background=None
    geo={};button_photos={}
    def label_photo(label,bg=PANEL,fg=INK):
        key=(label,bg,fg)
        if key not in button_photos:
            icon=Image.new('RGB',(96,26),bg)
            ImageDraw.Draw(icon).text((48,13),label,font=font(14,True),fill=fg,anchor='mm')
            button_photos[key]=ImageTk.PhotoImage(icon)
        return button_photos[key]
    def make_button(label,command):
        return tk.Button(root,text=label,command=command,takefocus=True,bg=PANEL,fg=INK,
            activebackground=ACCENT,relief='flat',borderwidth=0,highlightthickness=2,
            highlightbackground=BG,highlightcolor=GREEN,cursor='hand2')
    def toggle(event=None):
        nonlocal details
        if page=='cameras':details=not details
    def select(value):
        nonlocal page
        page=value
    def freeze():
        nonlocal frozen_view,frozen_at,fpga_scroll
        if frozen_view is None:
            frozen_view=freeze_view(state);frozen_at=time.monotonic()
        else:frozen_view=None;frozen_at=None
        fpga_scroll=0
    def selected_view():
        return frozen_view if page=='fpga' and frozen_view is not None else state
    def copy_diagnostic(event=None):
        nonlocal copied_until
        if page!='fpga':return
        import json
        root.clipboard_clear()
        root.clipboard_append(json.dumps(diagnostic_payload(selected_view(),frozen_view is not None),ensure_ascii=False,indent=2))
        copied_until=time.monotonic()+2
        return 'break'
    def scroll(event):
        nonlocal fpga_scroll
        if page!='fpga':return
        direction=-1 if getattr(event,'num',None)==4 or getattr(event,'delta',0)>0 else 1
        fpga_scroll=max(0,min(max(0,len(selected_view().board_history)-1),fpga_scroll+direction*2))
    button=make_button('参数详情',toggle)
    tabs={key:make_button(label,lambda key=key:select(key)) for key,label in (('cameras','实时画面'),('fpga','FPGA 协同'))}
    freeze_button=make_button('冻结显示',freeze)
    copy_button=make_button('复制诊断',copy_diagnostic)
    root.bind('<d>',toggle)
    root.bind('<KeyPress-1>',lambda event:select('cameras'))
    root.bind('<KeyPress-2>',lambda event:select('fpga'))
    root.bind('<Control-c>',copy_diagnostic)
    root.bind('<MouseWheel>',scroll);root.bind('<Button-4>',scroll);root.bind('<Button-5>',scroll)
    root.bind('<Escape>',lambda event: root.attributes('-fullscreen',False))
    root.bind('<F11>',lambda event: root.attributes('-fullscreen',not root.attributes('-fullscreen')))
    root.protocol('WM_DELETE_WINDOW',lambda:(stopping.set(),root.destroy()))
    def configure_button(widget,label,x,y,w,h,bg=PANEL,fg=INK):
        widget.configure(text=label,image=label_photo(label,bg,fg),compound='none',bg=bg)
        widget.place(x=x,y=y,width=w,height=h)
    def refresh():
        nonlocal background,geo
        if stopping.is_set():root.destroy();return
        packet=None
        try:
            from queue import Empty
            while True:packet=frames.get_nowait()
        except Empty:pass
        if packet is not None:state.ingest(packet)
        now=time.monotonic()
        w,h=max(860,root.winfo_width()),max(620,root.winfo_height())
        current=selected_view()
        display_time=frozen_at if page=='fpga' and frozen_at is not None else now
        image,geo,chart=render_dashboard(current,w,h,display_time,details,draw_chart=False,
            page=page,frozen=page=='fpga' and frozen_view is not None,fpga_scroll=fpga_scroll)
        background=ImageTk.PhotoImage(image)
        canvas.delete('all')
        canvas.create_image(0,0,image=background,anchor='nw')
        authority=geo.get('authority_value')
        if authority is not None and authority>0:
            x,y,r,b=geo['authority_bar'];end=x+(r-x)*authority
            fill='#71806D' if geo['authority_stale'] else GREEN
            if end-x>=8:
                canvas.create_rectangle(x+4,y,end-4,b,fill=fill,outline='')
                canvas.create_oval(x,y,x+8,b,fill=fill,outline='')
                canvas.create_oval(end-8,y,end,b,fill=fill,outline='')
            else:canvas.create_oval(x,y,end,b,fill=fill,outline='')
        if chart:
            stale=state.control is not None and state.control.run_state not in TERMINAL and now-state.control.sampled_mono_s>STALE_S
            color=MUTED if stale else GREEN
            for points in chart_geometry(state,chart,now):
                if len(points)>1:canvas.create_line(*[v for point in points for v in point],fill=color,width=2)
                if points:
                    x,y=points[-1];canvas.create_oval(x-3,y-3,x+3,y+3,fill=color,outline='')
        nx,ny=geo['tabs']
        for i,(key,label) in enumerate((('cameras','实时画面'),('fpga','FPGA 协同'))):
            configure_button(tabs[key],label,nx+i*112,ny,104,40,ACCENT if key==page else BG)
        if page=='cameras':
            bx,by,br,bb=geo['details_button']
            configure_button(button,'收起参数' if details else '参数详情',bx,by,br-bx,bb-by)
            freeze_button.place_forget();copy_button.place_forget()
        else:
            button.place_forget()
            right=geo['sidebar_x']-geo['gap'];main_w=right-geo['pad']
            y=geo['top']+4 if main_w>=720 else geo['top']+66
            configure_button(freeze_button,'继续实时' if frozen_view is not None else '冻结显示',right-224,y,108,40,
                ACCENT if frozen_view is not None else PANEL)
            configure_button(copy_button,'已复制' if copied_until>now else '复制诊断',right-108,y,108,40)
        root.after(200,refresh)
    root.update_idletasks()
    ready.send((True,None));ready.close()
    root.after(0,refresh)
    try:root.mainloop()
    finally:
        stopping.set()
        try:root.destroy()
        except Exception:pass
