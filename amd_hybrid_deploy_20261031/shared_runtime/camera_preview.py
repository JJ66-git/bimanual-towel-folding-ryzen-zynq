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

import sys
_RUNTIME_ROOT=str(Path(__file__).resolve().parents[1])
if _RUNTIME_ROOT not in sys.path:sys.path.insert(0,_RUNTIME_ROOT)

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


def _window(frames, stopping, ready, title, mode='LIVE', page='cameras'):
    try:
        from shared_runtime.dashboard_view import window
        window(frames, stopping, ready, title, mode, page)
    except BaseException as error:
        try: ready.send((False, type(error).__name__+': '+str(error)))
        except (BrokenPipeError, EOFError, OSError):
            print('CAMERA_PREVIEW_WINDOW_ERROR='+str(error), flush=True)
    finally:
        stopping.set()
        try: ready.close()
        except OSError: pass


class TaskCameraPreview:
    def __init__(self, title='FlexiFold · 双臂实时观察', fps=5., transform=None, mode='LIVE', page='cameras'):
        if not math.isfinite(fps) or fps <= 0 or fps > 30:
            raise ValueError('Invalid dashboard sampling rate')
        self.context=multiprocessing.get_context('spawn')
        self.frames=self.context.Queue(maxsize=1)
        self.frames.cancel_join_thread()
        self.stopping=self.context.Event()
        self.sampler_stop=threading.Event()
        self.state_lock=threading.Lock()
        self.process=None; self.sampler=None; self.closed=False
        self.mode=mode;self.page=page
        self.title=title; self.fps=fps; self.cameras=None; self.robot=None
        self.transform=transform; self.joints=None; self.last_joint_update=0.
        self.stats=dict(submitted_frames=0,dropped_frames=0,sample_errors=0,last_error=None,
                        state_updates=0,state_drops=0,pose_errors=0,feedback_errors=0,telemetry_errors=0)

    def start(self):
        parent,child=self.context.Pipe(duplex=False)
        self.process=self.context.Process(target=_window,args=(self.frames,self.stopping,child,self.title,self.mode,self.page),daemon=True)
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
            from shared_runtime.dashboard_telemetry import sample_display
            try:display=sample_display()
            except Exception:
                display={};self.stats['telemetry_errors']+=1
            packet=dict(**display,sampled=time.monotonic(),images=images,joints=joints,poses=poses,feedback_times=feedback_times)
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
