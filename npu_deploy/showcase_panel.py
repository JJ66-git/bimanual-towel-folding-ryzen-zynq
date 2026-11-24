#!/usr/bin/env python3
"""Interactive front end for existing scripts; a PTY keeps the terminal usable."""
import argparse
from pathlib import Path
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'amd_hybrid_deploy_20261031'))
_bootstrap_error=None
try:
    from shared_runtime.dashboard_view import BG,PANEL,INK,MUTED,LINE,GREEN,WARN,RED,ACCENT,font
except Exception as error:
    _bootstrap_error=error
from showcase_runner import Session,Status,TerminalBridge,WORKFLOWS,GATE_LABELS

TITLES={'start':'运行任务','prepare':'准备检查','reset':'仅复位','amd':'AMD 自检','interact':'互动'}
WORKFLOW_ORDER=('amd','prepare','reset','start','interact')


def interaction_controls_layout(width):
    half=(width-32)//2
    return dict(slider=(10,0,width-20,108),range=(44,111,width-88,42),
                challenge=(10,163,half,50),safe=(22+half,163,half,50),
                capture_title=(10,226,width-20,30),instruction=(10,260,width-20,44),
                leader=(10,308,width-20,40),capture=(10,354,half,64),save=(22+half,354,half,64),
                note=(10,430,width-20,56))


def button_image(label,bg=None,fg=None,width=300,size=13):
    from PIL import Image,ImageDraw
    bg=PANEL if bg is None else bg;fg=INK if fg is None else fg
    image=Image.new('RGB',(width,40),bg)
    ImageDraw.Draw(image).text((width/2,20),label,font=font(size,True),fill=fg,anchor='mm')
    return image


def render_panel(status,width,height,*,cpu_percent=None,demo=False,interaction=None,outcome_note=''):
    from PIL import Image,ImageDraw
    image=Image.new('RGB',(width,height),BG);draw=ImageDraw.Draw(image)
    p=24;x=p+222+20;r=width-p;w=r-x
    def text(x,y,value,size=14,color=INK,bold=False,mono=False,anchor=None):
        draw.text((x,y),str(value),font=font(size,bold,mono),fill=color,anchor=anchor)
    text(p,20,'FlexiFold',30,bold=True)
    text(p,62,'运行引导 · '+('SIMULATION' if demo else '原脚本实时日志'),13,MUTED)
    for i,key in enumerate(WORKFLOW_ORDER):
        bx=width-p-132*len(WORKFLOW_ORDER)+i*132
        draw.rounded_rectangle((bx,22,bx+124,74),radius=9,fill=ACCENT if key==status.workflow else PANEL)
        text(bx+62,36,TITLES[key],16,bold=True,anchor='ma')
    draw.line((p,102,r,102),fill=LINE)
    text(p,122,TITLES[status.workflow],21,bold=True)
    status_label={'idle':'尚未启动','starting':'载入原脚本','waiting':'等待阶段确认','running':'执行中',
        'stopping':'正在请求停止','finished':'流程已结束','stopped':'已停止','failed':'执行异常结束','io_error':'日志连接失败'}.get(status.state,status.state)
    if status.state=='finished':status_label={'start':'动作运行已结束','reset':'复位完成','prepare':'准备检查完成','amd':'平台自检完成'}.get(status.workflow,'流程已结束')
    text(p,160,status_label,13,RED if status.state in ('failed','io_error') else WARN if status.state=='stopping' else GREEN)
    for i,(key,title) in enumerate(WORKFLOWS[status.workflow][1]):
        y=205+i*77
        active=key==(status.gate or status.stage)
        color=GREEN if key in status.stages_done else WARN if active and status.state=='waiting' else INK if active else MUTED
        draw.ellipse((p,y+5,p+7,y+12),fill=color)
        text(p+20,y,title,15,color,active)
        subtitle='已完成' if key in status.stages_done else '等待确认' if status.gate==key else '当前阶段' if status.stage==key else '尚未执行'
        text(p+20,y+29,subtitle,11,color)
    draw.line((p,height-112,p+210,height-112),fill=LINE)
    text(p,height-95,'执行仍由原脚本负责',11,MUTED)
    text(p,height-71,'终端也可逐步操作',11,MUTED)

    cw=(w-28)/3
    cards=[('AMD CPU' if 'AMD' in status.cpu else 'CPU',
            '--' if cpu_percent is None else f'{cpu_percent:.1f}%',
            status.cpu,'整机 CPU 占用'),
           ('ROCm / PyTorch',status.hip if status.hip=='--' or status.hip.startswith('ROCm ') else 'HIP '+status.hip,
            status.gpu+(' / '+status.arch if status.arch!='--' else ''),
            'PyTorch '+status.torch),
           ('AMD NPU','推理已完成' if status.npu_inference and status.npu_partition else '子图已确认' if status.npu_partition else '设备已识别' if status.npu!='等待本轮检测' else '等待检测',
            '识别设备后验证实际推理' if status.npu=='等待本轮检测' else status.npu,
            'NPU 分区 / 推理分别验证')]
    if status.workflow=='interact':
        state=interaction or {};result=state.get('result') or {}
        verdict=('STOP' if result.get('mask') else 'PASS') if result else '等待检查'
        cards=[('右臂 J2 诊断目标',f"{state.get('requested_millirad',2650)/1000:.3f} rad",'上限 2.540 rad','只检查历史动作，不输出运动'),
               ('FPGA 实际判定',verdict,'目标越界' if result.get('mask',0)&1 else '未触发规则' if result else '拖动滑块后显示匹配回包','来源与整数参考必须同时匹配'),
               ('B35 连接','已匹配回包' if result else '客户端就绪' if state.get('ready') else '等待验证',f"SEQ {result.get('seq','--')} / SESSION {result.get('session','--')}",'无匹配回包时不显示通过')]
    if status.workflow=='reset':
        receipt=getattr(status,'reset_display',None) or {};phase=getattr(status,'reset_phase','')
        reset_label={'preparing':'准备复位','moving':'慢速复位中','ended':'复位已结束','error':'复位已中断','stopped':'复位已停止'}.get(phase,
            '复位已中断' if status.state=='failed' else '复位已停止' if status.state=='stopped' else '流程已结束' if status.state=='finished' else '准备复位' if status.state=='running' else '等待确认')
        acknowledged=bool(receipt.get('receipts') and receipt['receipts'][-1].get('phase')==receipt.get('phase') and receipt.get('error') is None)
        cards=[('双臂复位',reset_label,'原生慢速复位程序','到位状态来源：机械臂反馈'),
               ('FPGA 状态通道','已回执' if acknowledged else '回执未确认' if receipt else '等待本轮回执',
                '来源：主机复位状态','显示回执不等于物理保持确认'),
               ('运行范围','仅复位','复用已有准备结果','结束后不自动启动折叠策略')]
    for i,(label,value,detail,note) in enumerate(cards):
        cx=x+i*(cw+14)
        draw.rounded_rectangle((cx,120,cx+cw,258),radius=12,fill=PANEL)
        text(cx+16,131,label,14,bold=True)
        if i==1 and status.rocm_verified and status.workflow!='interact':text(cx+cw-16,133,'已实测',10,GREEN,anchor='ra')
        value_color=(RED if result.get('mask') else GREEN) if status.workflow=='interact' and i==1 and result else GREEN if (i==1 and status.rocm_verified) or (i==2 and status.npu_inference and status.npu_partition) else INK
        text(cx+16,158,value,25 if i==0 else 20 if i==1 else 21,value_color,mono=status.workflow!='reset' and i<2 and (status.workflow!='interact' or i==0))
        # Wrap actual model identifiers within the available width, never invent a shortened name.
        lines=[];line=''
        for char in detail:
            if font(11).getlength(line+char)>cw-32:
                lines.append(line);line=char
            else:line+=char
        if line:lines.append(line)
        for j,line in enumerate(lines[:2]):text(cx+16,197+j*17,line,11,MUTED)
        text(cx+16,239,note,10,MUTED)
    action_title=GATE_LABELS.get(status.gate,'载入流程后逐步确认' if status.state=='idle' else '等待原脚本返回' if status.state in ('running','starting') else status_label)
    if status.workflow=='reset' and not status.gate:action_title=reset_label
    ended=status.workflow=='start' and status.state in ('finished','failed','stopped')
    if status.workflow == 'start' and status.stage == 'return' and not ended:
        action_title = '双臂正在慢速归位'
    if status.workflow=='interact':action_title='拖动滑块调整右臂 J2 检查角度'
    if ended:
        run_label={'failed':'本轮执行中断','stopped':'本轮运行已停止'}.get(status.state,'动作运行已结束')
        if status.state == 'finished' and status.returned_home:
            run_label = '双臂已回到任务起始位'
        elif status.state == 'finished' and status.fold_finish_held:
            run_label = '折叠外形稳定，双臂已保持'
        action_title={'success':'现场确认：任务成功','failure':'现场确认：任务未完成'}.get(status.operator_outcome,run_label+'，结果待现场确认')
    authority=getattr(status,'authority_status',None)
    if authority and status.workflow=='start' and not ended:
        from shared_runtime.dashboard_view import authority_presenter
        ap=authority_presenter()(authority,fresh=status.last_output_mono is not None and time.monotonic()-status.last_output_mono<=1.5)
        action_title=ap['headline']+' · '+ap['mode_label']+' · #'+str(authority['target_sequence'])
    text(x,278,action_title,19,bold=True)
    motion_gate=status.gate in ('reset','run','return')
    note='此阶段会移动双臂，请确认现场条件后操作。' if motion_gate else '阶段失败或停止后不会自动进入后续步骤。'
    if status.gate=='return':
        note='任务步数已用完。双臂已保持；请查看夹爪和毛巾后确认慢速归位。'
    if status.workflow=='reset' and not status.gate:note='相机与 NPU 不重复准备；复位状态同步至现有 FPGA 服务。'
    if status.workflow=='interact':note=(interaction or {}).get('error') or '板端文件自动核对；服务可跨轮复用，退出互动后仍保持运行。'
    if authority and status.workflow=='start' and not ended:note=ap['sink_label']+' · '+ap['summary']
    if ended:note=outcome_note or '运行结束和动作规则通过，都不能代替实际折叠结果。'
    if ended and authority and getattr(status,'authority_fault',False) and not outcome_note:
        from shared_runtime.dashboard_view import authority_presenter
        ap=authority_presenter()(authority)
        note='核准中断：'+ap['reason_label']+' · '+ap['outcome_label']+' · 折叠结果待独立确认'
    text(x,309,note,12,WARN if motion_gate else MUTED)
    text(p if status.workflow=='interact' else x,450 if status.workflow=='interact' else 384,
         '运行日志' if status.workflow=='interact' else '确认现场结果' if ended else '实时终端',15,bold=True)
    age='' if status.last_output_mono is None else f'最近输出 {max(0.,time.monotonic()-status.last_output_mono):.1f}s 前 · '
    log_note=(f'窗口略过 {status.dropped_lines} 行 · ' if status.dropped_lines else '')+age+'完整输出另存 console.log'
    if status.workflow!='interact':text(r,387,log_note,11,MUTED,anchor='ra')
    text(x,height-45,'停止请求遵循原控制器退出流程，不是急停。',11,MUTED)
    if demo:text(r,height-45,'模拟流程 · 不连接相机、双臂或 FPGA',11,WARN,anchor='ra')
    return image,dict(console=(p,476,p+210,height-114) if status.workflow=='interact' else (x,508 if ended else 416,r,height-114),
        action=(p,300,210,64) if status.workflow=='interact' else (x,337,328,40),
        stop=(p,378,210,56) if status.workflow=='interact' else (r-144,337,144,40),
        interaction=(x,348,w,height-448),outcome=(x,414,w,70),
        selector=[(width-p-132*len(WORKFLOW_ORDER)+i*132,22,124,52) for i in range(len(WORKFLOW_ORDER))])


def demo_command(workflow):
    return [sys.executable,str(ROOT/'npu_deploy/showcase_panel_demo.py'),workflow]


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workflow',choices=WORKFLOWS,default='start')
    parser.add_argument('--demo',action='store_true')
    parser.add_argument('--snapshot',type=Path)
    parser.add_argument('--verify-ui',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if _bootstrap_error is not None:
        print('SHOWCASE_PANEL_UNAVAILABLE='+str(_bootstrap_error),file=sys.stderr);return 78
    if args.snapshot:
        status=Status(args.workflow,state='waiting',gate='reset',stage='fpga',cpu='AMD Ryzen AI 9 HX 370',
            hip='7.14.60850',torch='2.11.0+rocm7.14.1',gpu='AMD Radeon 890M / gfx1150',rocm_verified=True,
            npu='NPU Strix / aie2p',stages_done=['platform','fpga'])
        image,_=render_panel(status,1520,940,cpu_percent=12.4,demo=True)
        args.snapshot.parent.mkdir(parents=True,exist_ok=True);image.save(args.snapshot);return 0
    try:
        from shared_runtime.tk_support import load_tk
        tk=load_tk();root=tk.Tk()
        font(14);font(14,mono=True)
        from PIL import ImageTk
    except Exception as error:
        print('SHOWCASE_PANEL_UNAVAILABLE='+str(error),file=sys.stderr);return 78
    root.title('FlexiFold · 运行引导'+(' / SIMULATION' if args.demo else ''))
    root.geometry('1520x940');root.minsize(1000,940 if args.workflow=='interact' else 680);root.configure(bg=BG)
    canvas=tk.Canvas(root,bg=BG,highlightthickness=0);canvas.pack(fill='both',expand=True)
    console=tk.Text(root,bg='#17211C',fg='#E4EADD',insertbackground='#E4EADD',font=('Noto Sans Mono CJK SC',10),
        relief='flat',padx=14,pady=10,wrap='char',state='disabled',selectbackground='#38633A')
    scrollbar=tk.Scrollbar(root,command=console.yview);console.configure(yscrollcommand=scrollbar.set)
    session=None;bridge=None;workflow=args.workflow;closing=False;background=None;photos={};cpu_percent=None
    interaction_client=None;pending_workflow=None;slider_timer=None
    outcome_messages=queue.Queue();outcome_busy=False;outcome_note=''
    inventory={}
    output=queue.Queue(maxsize=400);stop_metrics=threading.Event();autoscroll=True;ui_errors=[]
    original_signals={kind:signal.getsignal(kind) for kind in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)}
    def metrics():
        nonlocal cpu_percent
        if args.demo:
            cpu_percent=12.4;return
        try:
            import importlib.metadata
            for line in Path('/proc/cpuinfo').read_text().splitlines():
                if line.startswith('model name'):
                    inventory['cpu']=line.split(':',1)[1].strip();break
            for package in ('torch','rocm'):
                try:inventory[package]=importlib.metadata.version(package)
                except importlib.metadata.PackageNotFoundError:pass
            import psutil
            psutil.cpu_percent(None)
            while not stop_metrics.wait(1):cpu_percent=psutil.cpu_percent(None)
        except Exception:pass
    threading.Thread(target=metrics,daemon=True).start()
    def photo(label,bg=PANEL,fg=INK,width=300,size=13):
        key=(label,bg,fg,width,size)
        if key not in photos:
            photos[key]=ImageTk.PhotoImage(button_image(label,bg,fg,width,size))
        return photos[key]
    def terminal_output():
        while not stop_metrics.is_set() or not output.empty():
            try:line=output.get(timeout=.1)
            except queue.Empty:continue
            try:print(line,flush=True)
            except (OSError,BrokenPipeError):pass
    threading.Thread(target=terminal_output,daemon=True).start()
    def append(lines):
        if not lines:return
        console.configure(state='normal');console.insert('end','\n'.join(lines)+'\n')
        if int(console.index('end-1c').split('.')[0])>2000:console.delete('1.0','500.0')
        console.configure(state='disabled')
        if autoscroll:console.see('end')
        for line in lines:
            try:output.put_nowait(line)
            except queue.Full:pass
    def select_workflow(value):
        nonlocal workflow,session,bridge,interaction_client,pending_workflow,outcome_note
        if outcome_busy:return
        if session and not session.finished.is_set():
            if value==workflow:return
            if workflow=='interact':
                state=interaction_client.snapshot() if interaction_client else {}
                if state.get('capture',{}).get('state')=='recording' or state.get('capture_busy'):
                    append(['请先点“结束并保存”，等待保存完成后再切换流程。']);return
                pending_workflow=value;session.stop()
            return
        if bridge:bridge.close();bridge=None
        if interaction_client:interaction_client.close();interaction_client=None
        workflow=value;session=None;outcome_note=''
        root.minsize(1000,940 if workflow=='interact' else 680)
        if workflow=='interact':start_or_confirm()
    def start_or_confirm():
        nonlocal session,bridge,interaction_client
        if outcome_busy:return
        if session is None:
            session=Session(workflow,command=demo_command(workflow) if args.demo else None)
            try:
                session.start();bridge=TerminalBridge(session);bridge.start()
            except Exception as error:
                append(['启动失败：'+str(error)])
                if session.process and session.process.poll() is None:session.stop()
                else:session=None
        else:
            status=session.snapshot()
            if session.finished.is_set():select_workflow(workflow)
            elif status.gate:session.confirm(status.generation)
    def stop():
        if session:session.stop()
        else:root.destroy()
    def close():
        nonlocal closing
        if outcome_busy:closing=True
        elif session and not session.finished.is_set():
            closing=True;session.stop()
        else:root.destroy()
    def send_secret(event=None):
        value=secret.get();secret.delete(0,'end')
        if session:session.authenticate(value)
    action=tk.Button(root,command=start_or_confirm,relief='flat',bg=ACCENT,activebackground=ACCENT,
        takefocus=True,highlightcolor=GREEN,highlightthickness=2,highlightbackground=BG)
    stop_button=tk.Button(root,command=stop,relief='flat',bg=PANEL,activebackground='#E7CFC9',
        takefocus=True,highlightcolor=RED,highlightthickness=2,highlightbackground=BG)
    selectors=[tk.Button(root,command=lambda key=key:select_workflow(key),relief='flat',bg=PANEL,
        takefocus=True,highlightcolor=GREEN,highlightthickness=2,highlightbackground=BG) for key in WORKFLOW_ORDER]
    secret=tk.Entry(root,show='*',bg=PANEL,fg=INK,insertbackground=INK)
    secret.bind('<Return>',send_secret)
    secret_button=tk.Button(root,text='提交终端认证',command=send_secret,relief='flat',bg=PANEL)
    interaction_frame=tk.Frame(root,bg=BG)
    instruction=tk.Entry(interaction_frame,font=('Noto Sans CJK SC',16),bg=PANEL,fg=INK,insertbackground=INK)
    instruction.insert(0,'示范抓住毛巾并轻轻抬起')
    capture_note=tk.Label(interaction_frame,text='',font=('Noto Sans CJK SC',12),bg=BG,fg=MUTED,anchor='w',justify='left')
    capture_title=tk.Label(interaction_frame,text='右主臂示范 · 任务指令',font=('Noto Sans CJK SC',16,'bold'),bg=BG,fg=INK,anchor='w')
    leader_status=tk.Label(interaction_frame,font=('Noto Sans CJK SC',13),bg=BG,anchor='w',justify='left')
    def send_angle():
        nonlocal slider_timer
        if slider_timer is not None:
            root.after_cancel(slider_timer)
            slider_timer=None
        if interaction_client and workflow=='interact' and interaction_client.snapshot()['ready']:
            interaction_client.angle(int(angle.get()))
    def schedule_angle(value):
        nonlocal slider_timer
        if interaction_client:interaction_client.preview_angle(int(float(value)))
        if slider_timer is not None:root.after_cancel(slider_timer)
        slider_timer=root.after(350,send_angle)
    angle=tk.Scale(interaction_frame,from_=1750,to=2650,resolution=10,orient='horizontal',command=schedule_angle,
        bg=BG,fg=INK,troughcolor='#D6E1D4',activebackground=ACCENT,
        highlightthickness=2,highlightbackground=BG,highlightcolor=GREEN,
        showvalue=False,sliderlength=68,width=52,relief='flat',takefocus=True)
    angle.set(2650);angle.bind('<ButtonRelease-1>',lambda event:send_angle())
    range_bar=tk.Canvas(interaction_frame,bg=BG,highlightthickness=0)
    challenge=tk.Button(interaction_frame,command=lambda:angle.set(2650),relief='flat',bg='#E7CFC9',activebackground='#E7CFC9',takefocus=True)
    safe_button=tk.Button(interaction_frame,command=lambda:angle.set(2540),relief='flat',bg=PANEL,activebackground=PANEL,takefocus=True)
    def capture_start():
        if interaction_client:interaction_client.capture_start(instruction.get())
    def capture_stop():
        if interaction_client:interaction_client.capture_stop()
    capture_start_button=tk.Button(interaction_frame,command=capture_start,relief='flat',bg=ACCENT,activebackground=ACCENT,takefocus=True)
    capture_stop_button=tk.Button(interaction_frame,command=capture_stop,relief='flat',bg=PANEL,activebackground=PANEL,takefocus=True)
    outcome_frame=tk.Frame(root,bg=BG)
    def confirm_outcome(success):
        nonlocal outcome_busy,outcome_note
        if outcome_busy or args.demo or not session or not session.finished.is_set():return
        receipt=session.snapshot().fpga_task_end
        if not receipt:return
        job=session;outcome_busy=True;outcome_note='正在发送现场确认……'
        def send():
            try:
                from task_outcome import confirm
                result=confirm(receipt['address'],receipt['session'],success)
                outcome_messages.put((job,'success' if success else 'failure',result))
            except Exception as error:outcome_messages.put((job,None,str(error)))
        threading.Thread(target=send,daemon=True).start()
    confirm_success=tk.Button(outcome_frame,command=lambda:confirm_outcome(True),relief='flat',bg=ACCENT,activebackground=ACCENT,takefocus=True)
    confirm_failure=tk.Button(outcome_frame,command=lambda:confirm_outcome(False),relief='flat',bg='#E7CFC9',activebackground='#E7CFC9',takefocus=True)
    follow=tk.Button(root,text='暂停跟随',relief='flat',bg=BG)
    def toggle_follow():
        nonlocal autoscroll
        autoscroll=not autoscroll;follow.configure(text='暂停跟随' if autoscroll else '继续跟随')
    follow.configure(command=toggle_follow)
    root.protocol('WM_DELETE_WINDOW',close)
    signal.signal(signal.SIGINT,lambda *unused:stop())
    signal.signal(signal.SIGTERM,lambda *unused:close())
    signal.signal(signal.SIGHUP,lambda *unused:close())
    root.bind('<Escape>',lambda event:stop())
    def refresh():
        nonlocal background,session,bridge,pending_workflow,interaction_client,outcome_busy,outcome_note
        while True:
            try:job,value,result=outcome_messages.get_nowait()
            except queue.Empty:break
            outcome_busy=False
            if job is session:
                if value:
                    with session.lock:session.status.operator_outcome=value
                    outcome_note='板端已接收现场确认 · 来源：操作员'
                    append(['OPERATOR_OUTCOME='+json.dumps(result,ensure_ascii=False)])
                else:
                    outcome_note='同步未完成，请重试；板屏结果未确认。'
                    append(['OPERATOR_OUTCOME_FAILED='+str(result)])
        status=session.snapshot() if session else Status(workflow)
        if not args.demo:
            if status.cpu=='等待检测' and inventory.get('cpu'):status.cpu=inventory['cpu']
            if status.torch=='--' and inventory.get('torch'):status.torch=inventory['torch']
            if status.hip=='--' and inventory.get('rocm'):
                status.hip='ROCm '+inventory['rocm']
                status.gpu='框架已安装 · GPU 待实测'
        if session:
            append(session.drain())
            if session.finished.is_set() and bridge:bridge.close();bridge=None
        if pending_workflow and session and session.finished.is_set():
            desired=pending_workflow;pending_workflow=None;select_workflow(desired);status=Status(workflow)
        if closing and not outcome_busy and (session is None or session.finished.is_set()):root.destroy();return
        if (workflow=='interact' and not args.demo and session and not session.finished.is_set()
                and status.interaction_verified and status.interaction_server_ready and interaction_client is None):
            from showcase_interaction import InteractionBridge
            interaction_client=InteractionBridge();interaction_client.start()
        interaction=interaction_client.snapshot() if interaction_client else {}
        if workflow=='interact':interaction['requested_millirad']=int(angle.get())
        if not session or session.finished.is_set():interaction.update(ready=False,result=None)
        image,geo=render_panel(status,max(1000,root.winfo_width()),max(680,root.winfo_height()),cpu_percent=cpu_percent,demo=args.demo,interaction=interaction,outcome_note=outcome_note)
        background=ImageTk.PhotoImage(image);canvas.delete('all');canvas.create_image(0,0,image=background,anchor='nw')
        x,y,r,b=geo['console'];console.place(x=x,y=y,width=r-x-16,height=b-y);scrollbar.place(x=r-16,y=y,width=16,height=b-y)
        x,y,w,h=geo['action']
        enabled=not outcome_busy and (session is None or session.finished.is_set() or status.gate is not None)
        label='载入'+TITLES[workflow] if session is None else '返回流程选择' if session.finished.is_set() else GATE_LABELS.get(status.gate,'正在执行')
        if workflow=='interact' and session:
            label='重新载入互动' if session.finished.is_set() else '互动已就绪' if interaction.get('ready') else '正在载入互动'
        action.configure(image=photo(label,ACCENT,width=w-12,size=18 if workflow=='interact' else 15),state='normal' if enabled else 'disabled');action.place(x=x,y=y,width=w,height=h)
        x,y,w,h=geo['stop'];stop_button.configure(image=photo('请求停止',PANEL,RED,w-12,size=16),state='normal' if session and not session.finished.is_set() else 'disabled');stop_button.place(x=x,y=y,width=w,height=h)
        for widget,key,rect in zip(selectors,WORKFLOW_ORDER,geo['selector']):
            x,y,w,h=rect;widget.configure(text=TITLES[key],image=photo(TITLES[key],ACCENT if key==workflow else PANEL,width=w-10,size=16),
                state='normal' if not outcome_busy and (session is None or session.finished.is_set() or workflow=='interact') else 'disabled');widget.place(x=x,y=y,width=w,height=h)
        height=root.winfo_height()
        if workflow=='interact':
            left,top,width,frame_height=geo['interaction']
            interaction_frame.place(x=left,y=top,width=width,height=frame_height)
            layout=interaction_controls_layout(width)
            def place(widget,key):
                px,py,pw,ph=layout[key];widget.place(x=px,y=py,width=pw,height=ph)
            for widget,key in ((angle,'slider'),(range_bar,'range'),(challenge,'challenge'),(safe_button,'safe'),
                               (capture_title,'capture_title'),(instruction,'instruction'),
                               (capture_start_button,'capture'),(capture_stop_button,'save'),
                               (leader_status,'leader'),(capture_note,'note')):place(widget,key)
            range_width=layout['range'][2]
            range_bar.delete('all');boundary=range_width*(2540-1750)/(2650-1750)
            range_bar.create_rectangle(0,0,boundary,10,fill=GREEN,outline='')
            range_bar.create_rectangle(boundary,0,range_width,10,fill=RED,outline='')
            for position,label,anchor,color in ((0,'1.750','nw',MUTED),
                    (boundary-5,'2.540 上限','ne',GREEN),(range_width,'2.650','ne',RED)):
                range_bar.create_text(position,15,text=label,anchor=anchor,fill=color,font=('Noto Sans CJK SC',12))
            capture=interaction.get('capture') or {};recording=capture.get('state')=='recording'
            ready=bool(interaction.get('ready')) and not args.demo
            capture_busy=interaction.get('capture_busy',False)
            capture_error=interaction.get('capture_error','')
            capture_label='检查中…' if capture_busy and not recording else '插好了，检查并开始'
            for widget,key,label,color in ((challenge,'challenge','越界示例','#E7CFC9'),(safe_button,'safe','上限示例',PANEL),
                                            (capture_start_button,'capture',capture_label,ACCENT),(capture_stop_button,'save','结束并保存',PANEL)):
                widget.configure(text=label,image=photo(label,color,width=layout[key][2]-12,size=16 if key in ('challenge','safe') else 20))
            for widget in (angle,challenge,safe_button):widget.configure(state='normal' if ready else 'disabled')
            capture_start_button.configure(state='normal' if ready and not recording and not capture_busy else 'disabled')
            capture_stop_button.configure(state='normal' if ready and recording and not capture_busy else 'disabled')
            if args.demo:
                leader_message='模拟面板未连接右主臂。'
            elif not ready:
                leader_message='请插好主臂电源。互动就绪后即可检查。'
            elif capture_error:
                leader_message=capture_error
            elif capture_busy:
                leader_message='正在保存本次示范…' if recording else '正在检查右主臂信号…'
            elif recording:
                leader_message='正在录制。请操作主臂和夹爪，完成后点击「结束并保存」。'
            else:
                leader_message='请插好主臂电源再开始。'
            leader_status.configure(text=leader_message,fg=RED if capture_error else GREEN if recording else INK,
                                    wraplength=width-20)
            note=''
            if capture.get('episode_id'):
                source='合成排练' if capture.get('source')=='synthetic_rehearsal' else '真实主臂'
                states={'recording':'正在采集','complete':'已保存','interrupted':'采集中断','save_error':'保存失败'}
                capture_state=states.get(capture.get('state'),capture.get('state',''))
                note=f"{source} · {capture_state} · {capture.get('sample_count',0)} 条动作"
                if capture.get('error'):note+='\n'+capture['error']
                elif capture.get('state')=='complete' and capture.get('saved_dir'):
                    path=Path(capture['saved_dir'])
                    note+=f'\n保存位置：{path.parent.name}/{path.name}'
            capture_note.configure(text=note,wraplength=max(200,width-20))
        else:interaction_frame.place_forget()
        ended=workflow=='start' and status.state in ('finished','failed','stopped')
        if ended:
            left,top,width,frame_height=geo['outcome'];outcome_frame.place(x=left,y=top,width=width,height=frame_height)
            half=(width-16)//2
            enabled=bool(status.fpga_task_end) and not outcome_busy and not args.demo and session and session.finished.is_set()
            for widget,bx,label,color in ((confirm_success,0,'现场确认：成功',ACCENT),(confirm_failure,half+16,'现场确认：未完成','#E7CFC9')):
                widget.configure(image=photo(label,color,width=half-12,size=20),state='normal' if enabled else 'disabled')
                widget.place(x=bx,y=0,width=half,height=64)
        else:outcome_frame.place_forget()
        follow.place(x=r-130,y=height-98,width=126,height=30)
        if status.secret_input:
            secret.place(x=geo['console'][0],y=height-98,width=230,height=30)
            secret_button.place(x=geo['console'][0]+240,y=height-98,width=150,height=30)
        else:secret.place_forget();secret_button.place_forget()
        root.after(200,refresh)
    def report_callback_exception(kind,value,traceback):
        ui_errors.append(str(value));append(['PANEL_ERROR='+str(value)])
        if session and not session.finished.is_set():session.stop()
    root.report_callback_exception=report_callback_exception
    if args.verify_ui:
        if not args.demo:parser.error('--verify-ui requires --demo')
        from shared_runtime.dashboard_capture import capture
        directory=ROOT/'npu_deploy/artifacts/launcher_ui';directory.mkdir(parents=True,exist_ok=True)
        captures=[]
        seen=[None]
        def finish_capture():
            capture(root).save(directory/'finished.png');captures.append('finished')
            (directory/'verification.json').write_text(json.dumps(dict(captures=captures,ui_errors=ui_errors,status=vars(session.snapshot())),indent=2))
            root.after(200,close)
        def capture_gate(generation,key):
            if session.snapshot().generation==generation and session.snapshot().gate==key:
                capture(root).save(directory/(key+'.png'));captures.append(key)
                start_or_confirm()
        def automate():
            if session is None:start_or_confirm()
            elif session.finished.is_set():
                root.after(250,finish_capture);return
            elif session.snapshot().gate:
                status=session.snapshot()
                if seen[0]!=status.generation:
                    seen[0]=status.generation
                    root.after(250,lambda generation=status.generation,key=status.gate:capture_gate(generation,key))
            root.after(500,automate)
        root.after(800,automate)
        root.after(20000,close)
    if workflow=='interact' and not args.verify_ui:root.after(0,start_or_confirm)
    root.after(0,refresh)
    try:root.mainloop()
    finally:
        stop_metrics.set()
        if interaction_client:interaction_client.close()
        if session and not session.finished.is_set():
            session.stop()
            # No forced kill: allow the original controller to finish its hold/cleanup.
            session.finished.wait()
        if bridge:bridge.close()
        for kind,handler in original_signals.items():signal.signal(kind,handler)
    if ui_errors:return 1
    code=session.snapshot().returncode if session else 0
    return (128-code if code is not None and code<0 else code) or 0


if __name__=='__main__':raise SystemExit(main())
