"""Read-only task dashboard; reuses camera and Piper SDK caches.

The window is a separate process. This module never opens a camera or CAN
interface and never sends control messages. Values are display-only.
"""
import math
import multiprocessing
from functools import lru_cache
from pathlib import Path
from queue import Empty, Full
import threading
import time

ROLES = ('left', 'middle', 'right')
SIDES = ('left', 'right')
JOINT_KEYS = tuple(f'{side}_joint_{i}.pos' for side in SIDES for i in range(1, 7))
GRIP_KEYS = tuple(f'{side}_gripper.pos' for side in SIDES)
POSE_FIELDS = ('X_axis', 'Y_axis', 'Z_axis', 'RX_axis', 'RY_axis', 'RZ_axis')


def capture_joint_state(observation, timestamp):
    """Copy the 14 values actually used by the policy, in explicit units."""
    values = [float(observation[key]) for key in JOINT_KEYS + GRIP_KEYS]
    if not all(math.isfinite(value) for value in values):
        raise ValueError('Non-finite joint observation')
    return dict(timestamp=timestamp,
        left_joints=tuple(values[:6]), right_joints=tuple(values[6:12]),
        left_gripper_mm=values[12]*1000, right_gripper_mm=values[13]*1000)


def read_sdk_pose(bus):
    """Read existing SDK cache, not a request or a synchronized 6-D sample."""
    msg = bus.piper.GetArmEndPoseMsgs()
    stamp = float(msg.time_stamp)
    if not math.isfinite(stamp) or stamp <= 0:
        return None
    raw = msg.end_pose
    values = tuple(float(getattr(raw, field))*0.001 for field in POSE_FIELDS)
    if not all(math.isfinite(value) for value in values):
        return None
    return dict(timestamp=stamp, xyz_mm=values[:3], rotation_deg=values[3:])


def read_sdk_feedback_times(bus):
    """Report CAN cache receipt timestamps; no new feedback is requested."""
    joint = float(bus.piper.GetArmJointMsgs().time_stamp)
    gripper = float(bus.piper.GetArmGripperMsgs().time_stamp)
    return dict(joint=joint if math.isfinite(joint) and joint > 0 else None,
                gripper=gripper if math.isfinite(gripper) and gripper > 0 else None)


def feedback_fresh(stamp, now, limit=1.5):
    return stamp is not None and -2 <= now-stamp <= limit


@lru_cache(maxsize=16)
def dashboard_font(size, bold=False, mono=False):
    """Pillow draws Chinese directly; Tk's X11 backend may use bitmap fonts."""
    from PIL import ImageFont
    base = Path('/usr/share/fonts/opentype/noto')
    path = base / ('NotoSansCJK-Bold.ttc' if bold else 'NotoSansCJK-Regular.ttc')
    if not path.is_file():
        raise RuntimeError('缺少 Noto CJK 字体：'+str(path))
    return ImageFont.truetype(str(path), max(12, round(size*1.7)), index=7 if mono else 2)


def render_dashboard_text(text, foreground, background, size=11, bold=False, mono=False):
    """Render text using the actual CJK font rather than the Tk font backend."""
    from PIL import Image, ImageDraw
    font = dashboard_font(size, bold=bold, mono=mono)
    text = str(text) or ' '
    probe = Image.new('RGB',(1,1),background)
    box = ImageDraw.Draw(probe).multiline_textbbox((0,0),text,font=font,spacing=3)
    image = Image.new('RGB',(max(1,box[2]-box[0]+2),max(1,box[3]-box[1]+2)),background)
    ImageDraw.Draw(image).multiline_text((-box[0]+1,-box[1]+1),text,fill=foreground,font=font,spacing=3)
    return image


def _window(frames, stopping, ready, title):
    root = None
    notified = False
    try:
        import tkinter as tk
        from PIL import Image, ImageTk

        bg, card, border = '#0a1523', '#14283b', '#29445b'
        fg, muted, cyan, yellow = '#e8f1f7', '#96aebe', '#5dd9e4', '#ffd17a'
        text_labels = []  # Keep Tk image references alive for the window lifetime.
        class TextLabel:
            def __init__(self, parent, *, text, bg, fg, size=11, bold=False, mono=False, anchor='w'):
                self.widget=tk.Label(parent, bg=bg, anchor=anchor, borderwidth=0)
                self.bg,self.fg,self.size,self.bold,self.mono=bg,fg,size,bold,mono
                self.text=None;self.photo=None
                text_labels.append(self)
                self.configure(text=text)
            def configure(self, *, text=None, fg=None):
                new_text=self.text if text is None else text
                new_fg=self.fg if fg is None else fg
                if new_text==self.text and new_fg==self.fg: return
                self.text,self.fg=new_text,new_fg
                image=render_dashboard_text(self.text,self.fg,self.bg,self.size,self.bold,self.mono)
                self.photo=ImageTk.PhotoImage(image)
                self.widget.configure(image=self.photo, text='')
            def grid(self,*args,**kwargs): return self.widget.grid(*args,**kwargs)
            def pack(self,*args,**kwargs): return self.widget.pack(*args,**kwargs)
        root = tk.Tk()
        root.title(title)
        root.configure(background=bg)
        root.minsize(900, 600)
        screen_width, screen_height = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f'{min(1540,screen_width)}x{min(930,screen_height)}')
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)
        heading = tk.Frame(root, bg=bg)
        heading.grid(row=0, column=0, sticky='ew', padx=18, pady=(12, 8))
        TextLabel(heading, text='PIPER 双臂 · 实时状态', bg=bg, fg=fg,
                  size=18, bold=True).pack(side='left')
        status = TextLabel(heading, text='等待任务连接', bg=bg, fg=yellow, size=11)
        status.pack(side='right')

        body = tk.Frame(root, bg=bg)
        body.grid(row=1, column=0, sticky='nsew', padx=15)
        for column in range(3): body.columnconfigure(column, weight=1, uniform='camera')
        body.rowconfigure(1, weight=1)
        camera_labels, camera_status = {}, {}
        for column, (role, caption) in enumerate(zip(ROLES, ('左视角  LEFT', '中央视角  MIDDLE', '右视角  RIGHT'))):
            box = tk.Frame(body, bg=card, highlightbackground=border, highlightthickness=1)
            box.grid(row=0, column=column, rowspan=2, padx=4, sticky='nsew')
            box.columnconfigure(0, weight=1); box.rowconfigure(1, weight=1)
            TextLabel(box, text=caption, bg=card, fg=cyan, size=11, bold=True).grid(row=0,column=0,sticky='ew',padx=12,pady=8)
            label = tk.Label(box, bg='#0c1c29', anchor='center')
            label.grid(row=1,column=0,sticky='nsew',padx=6,pady=(0,6))
            camera_status[role] = TextLabel(box, text='等待相机', bg=card, fg=yellow, size=9)
            camera_status[role].grid(row=2, column=0, sticky='ew', padx=12, pady=(0,5))
            camera_labels[role] = label
        values = tk.Frame(root, bg=bg)
        values.grid(row=2, column=0, sticky='ew', padx=15, pady=(10, 5))
        values.columnconfigure(0, weight=1, uniform='arm'); values.columnconfigure(1, weight=1, uniform='arm')
        joint_labels, pose_labels = {}, {}
        for column, (side, caption) in enumerate((('left','左臂  LEFT ARM'),('right','右臂  RIGHT ARM'))):
            panel = tk.Frame(values, bg=card, highlightbackground=border, highlightthickness=1)
            panel.grid(row=0, column=column, padx=4, sticky='nsew')
            TextLabel(panel, text=caption, bg=card, fg=cyan, size=12, bold=True).pack(fill='x',padx=12,pady=(9,3))
            joint_labels[side] = TextLabel(panel, text='关节 / 夹爪：等待反馈', bg=card, fg=fg,
                size=11, mono=True)
            joint_labels[side].pack(fill='x',padx=12,pady=3)
            pose_labels[side] = TextLabel(panel, text='末端位姿：等待 SDK 反馈', bg=card, fg=muted,
                size=11, mono=True)
            pose_labels[side].pack(fill='x',padx=12,pady=(3,10))
        TextLabel(root, text='关节：rad   夹爪：mm   末端：SDK 原始坐标系，XYZ mm / 欧拉角 °   |   数据不同步，仅供展示',
                  bg=bg, fg=muted, size=10).grid(row=3,column=0,pady=3)
        TextLabel(root, text='关闭窗口只关闭展示；停止任务请在运行终端按 Ctrl+C',
                  bg=bg, fg=yellow, size=10).grid(row=4,column=0,pady=(0,10))
        root.protocol('WM_DELETE_WINDOW', lambda: (stopping.set(), root.destroy()))
        photos = {}
        camera_time = None
        camera_times = {}
        joint_state = None
        poses = {}
        feedback_times = {}

        def refresh():
            nonlocal camera_time, joint_state, poses, feedback_times
            if stopping.is_set():
                root.destroy(); return
            packet = None
            try:
                while True: packet = frames.get_nowait()
            except Empty:
                pass
            if packet is not None:
                camera_time = packet['sampled'] if packet['images'] else camera_time
                joint_state = packet['joints'] or joint_state
                poses = packet['poses']
                feedback_times = packet['feedback_times']
                for role, image in packet['images'].items():
                    camera_times[role] = packet['sampled']
                    label = camera_labels[role]
                    width = max(180, label.winfo_width())
                    height = max(140, label.winfo_height())
                    frame = Image.fromarray(image)
                    frame.thumbnail((width,height), Image.Resampling.BILINEAR)
                    photos[role] = ImageTk.PhotoImage(frame)
                    label.configure(image=photos[role], text='')
            now = time.monotonic()
            for role in ROLES:
                camera_age = now-camera_times[role] if role in camera_times else None
                camera_status[role].configure(
                    text='等待相机' if camera_age is None else f'取样 {max(0,camera_age):.1f} 秒前' + (' · 画面过期' if camera_age > 1.5 else ''),
                    fg=cyan if camera_age is not None and camera_age < 1.5 else yellow)
            age = now-camera_time if camera_time is not None else None
            status.configure(text=('等待相机连接' if age is None else f'画面最近取样 {max(0,age):.1f} 秒前'),
                             fg=cyan if age is not None and age < 1.5 else yellow)
            for side in SIDES:
                if joint_state:
                    receipts = feedback_times.get(side) or {}
                    joint_fresh = feedback_fresh(receipts.get('joint'), time.time())
                    grip_fresh = feedback_fresh(receipts.get('gripper'), time.time())
                    stale = now-joint_state['timestamp'] > 1.5 or not (joint_fresh and grip_fresh)
                    detail = '反馈正常' if not stale else '反馈过期或未知'
                    joints = joint_state[side+'_joints']
                    gripper = joint_state[side+'_gripper_mm']
                    joint_labels[side].configure(text='关节 (rad)  '+detail+f'  夹爪 {gripper:.1f} mm\n'
                        + ' '.join(f'J{i+1}:{v:+.3f}' for i,v in enumerate(joints[:3]))+'\n'
                        + ' '.join(f'J{i+4}:{v:+.3f}' for i,v in enumerate(joints[3:])),
                        fg=yellow if stale else fg)
                pose = poses.get(side)
                if pose:
                    age = time.time()-pose['timestamp']
                    stale = age < -2 or age > 1.5
                    xyz, rotation = pose['xyz_mm'], pose['rotation_deg']
                    pose_labels[side].configure(text=('末端位姿  '+('反馈过期' if stale else 'SDK 缓存')+'\n'
                        + 'XYZ mm   '+ '  '.join(f'{v:+.1f}' for v in xyz)+'\n'
                        + 'RX RY RZ °  '+ '  '.join(f'{v:+.1f}' for v in rotation)), fg=yellow if stale else muted)
                else:
                    pose_labels[side].configure(text='末端位姿：暂无有效 SDK 反馈',fg=yellow)
            root.after(100, refresh)

        root.update_idletasks()
        ready.send((True, None)); notified=True; ready.close()
        root.after(0,refresh); root.mainloop()
    except BaseException as error:
        if not notified:
            try: ready.send((False,type(error).__name__+': '+str(error)))
            except (BrokenPipeError, EOFError, OSError): pass
        else: print('CAMERA_PREVIEW_WINDOW_ERROR='+str(error),flush=True)
    finally:
        stopping.set()
        try: ready.close()
        except OSError: pass
        if root is not None:
            try: root.destroy()
            except Exception: pass


class TaskCameraPreview:
    def __init__(self, title='双臂实时状态', fps=5., transform=None):
        if not math.isfinite(fps) or fps <= 0 or fps > 30:
            raise ValueError('Invalid dashboard sampling rate')
        self.context=multiprocessing.get_context('spawn')
        self.frames=self.context.Queue(maxsize=1)
        self.frames.cancel_join_thread()
        self.stopping=self.context.Event()
        self.sampler_stop=threading.Event()
        self.state_lock=threading.Lock()
        self.process=None; self.sampler=None; self.closed=False
        self.title=title; self.fps=fps; self.cameras=None; self.robot=None
        self.transform=transform; self.joints=None; self.last_joint_update=0.
        self.stats=dict(submitted_frames=0,dropped_frames=0,sample_errors=0,last_error=None,
                        state_updates=0,state_drops=0,pose_errors=0,feedback_errors=0)

    def start(self):
        parent,child=self.context.Pipe(duplex=False)
        self.process=self.context.Process(target=_window,args=(self.frames,self.stopping,child,self.title),daemon=True)
        try:
            self.process.start();child.close()
            if not parent.poll(10): raise RuntimeError('Dashboard window did not become ready')
            ok,error=parent.recv()
            if not ok: raise RuntimeError('Cannot open dashboard: '+str(error))
        except BaseException:
            self.close(); raise
        finally:
            parent.close();child.close()

    def attach(self,cameras,robot=None):
        if self.sampler is not None: raise RuntimeError('Dashboard already attached')
        if any(role not in cameras for role in ROLES): raise ValueError('Dashboard requires three cameras')
        self.cameras={role:cameras[role] for role in ROLES};self.robot=robot
        self.sampler=threading.Thread(target=self._sample_loop,name='task_dashboard_sampler',daemon=True)
        self.sampler.start()

    def update_observation(self, observation):
        now=time.monotonic()
        if self.closed or self.stopping.is_set() or now-self.last_joint_update < 1./self.fps:
            return False
        if not self.state_lock.acquire(blocking=False):
            self.stats['state_drops']+=1; return False
        try:
            self.joints=capture_joint_state(observation,now)
            self.last_joint_update=now; self.stats['state_updates']+=1
            return True
        finally:
            self.state_lock.release()

    def sample_once(self):
        if self.closed or self.stopping.is_set(): return False
        try:
            images={}
            for role in ROLES:
                try:
                    image=self.cameras[role].read_latest(max_age_ms=500)
                    if image is None or len(image.shape)!=3 or image.shape[2]!=3:
                        raise ValueError('Invalid RGB image')
                    images[role]=image.copy()
                except Exception:
                    pass  # Keep other camera and vector data available.
            if self.transform is not None and len(images)==3:
                images=self.transform(images)
            poses={};feedback_times={}
            for side in SIDES:
                try:
                    if self.robot is not None:
                        poses[side]=read_sdk_pose(getattr(self.robot,side+'_bus'))
                except Exception:
                    self.stats['pose_errors']+=1
                try:
                    if self.robot is not None:
                        feedback_times[side]=read_sdk_feedback_times(getattr(self.robot,side+'_bus'))
                except Exception:
                    self.stats['feedback_errors']+=1
            with self.state_lock: joints=self.joints
            packet=dict(sampled=time.monotonic(),images=images,joints=joints,poses=poses,feedback_times=feedback_times)
            self.frames.put_nowait(packet)
            self.stats['submitted_frames']+=1
            return True
        except Full:
            self.stats['dropped_frames']+=1
            return False
        except Exception as error:
            self.stats['sample_errors']+=1;self.stats['last_error']=type(error).__name__+': '+str(error)
            return False

    def _sample_loop(self):
        while not self.sampler_stop.is_set() and not self.stopping.is_set():
            self.sample_once()
            self.sampler_stop.wait(1./self.fps)

    def close(self):
        if self.closed:return
        self.closed=True;self.sampler_stop.set();self.stopping.set()
        if self.sampler is not None:self.sampler.join(timeout=1)
        if self.process is not None and self.process.pid is not None:
            self.process.join(timeout=2)
            if self.process.is_alive():self.process.terminate();self.process.join(timeout=1)
        self.frames.close()
