"""LCD rendering in a separate process; the UDP/guard owner never paints pixels.

Only the child maps the LCD IP, using its existing address, without downloading an
overlay. At most one snapshot is in flight; intermediate visual snapshots may be
coalesced, while the parent retains all counters and terminal state.
"""
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import pickle
import time
import zlib

from screen.cooperative_state import CooperativeState


def _worker(receiver,sender,descriptor,directory,mode,initial):
    display=None;generation=0
    directory=Path(directory)
    def progress(stage,**fields):
        row=dict(stage=stage,renderer_pid=os.getpid(),monotonic_ns=time.monotonic_ns(),**fields)
        temporary=directory/'lcd_worker_startup.tmp.json';temporary.write_text(json.dumps(row)+'\n')
        temporary.replace(directory/'lcd_worker_startup.json')
    try:
        progress('importing_pynq')
        from pynq import MMIO
        from types import SimpleNamespace
        from screen.lcd import Framebuffer
        from screen.cooperative_view import PALETTE
        if mode=='booth':from board.booth_display import render
        else:from screen.cooperative_view import render
        mmio=MMIO(descriptor['base_addr'],descriptor['length'])
        ip=SimpleNamespace(mmio=mmio,read=mmio.read,write=mmio.write)
        progress('rendering_initial_frame')
        image=render(initial);progress('initializing_framebuffer')
        display=Framebuffer(ip,palette=PALETTE,initial_image=image)
        def paint(state):
            nonlocal generation
            image=render(state);frame=display.present(image);generation+=1
            name='booth_lcd' if mode=='booth' else 'lcd_latest'
            temporary=directory/(name+'.tmp.png');image.save(temporary)
            row=dict(state,framebuffer=frame,display_generation=generation,renderer_pid=os.getpid(),render_worker='process',
                     image_sha256=hashlib.sha256(temporary.read_bytes()).hexdigest())
            temporary.replace(directory/(name+'.png'))
            temporary=directory/(name+'.tmp.json');temporary.write_text(json.dumps(row,ensure_ascii=False)+'\n')
            temporary.replace(directory/(name+'.json'))
        progress('presenting_first_frame')
        paint(initial);progress('ready');sender.send(dict(ready=True,pid=os.getpid(),generation=generation))
        while True:
            operation,state=pickle.loads(zlib.decompress(receiver.recv_bytes()))
            paint(state)
            sender.send(dict(ack=True,pid=os.getpid(),generation=generation))
            if operation=='close':break
    except EOFError:pass
    except BaseException as error:
        try:progress('error',error=type(error).__name__+': '+str(error))
        except OSError:pass
        try:sender.send(dict(error=type(error).__name__+': '+str(error),pid=os.getpid()))
        except (OSError,EOFError):pass
    finally:
        if display is not None:display.close(preserve=True)
        receiver.close();sender.close()


class IsolatedDisplay:
    def __init__(self,ip,directory,source,booth=False,*,context=None,worker_target=_worker,interval=.5,startup_timeout=45):
        self.mode='booth' if booth else 'cooperative';self.source=source
        self.model=CooperativeState(source) if not booth else None
        self.booth_state={'source':source};self._error=None;self.closed=False
        self.inflight=False;self.interval=interval;self.next_publish=0.;self.child_pid=None
        self.ready=False;self.startup_deadline=time.monotonic()+startup_timeout
        descriptor=dict(base_addr=int(ip.mmio.base_addr),length=int(ip.mmio.length))
        if descriptor['base_addr']<0 or descriptor['length']<0x61000:raise ValueError('Invalid LCD MMIO descriptor')
        ctx=context or multiprocessing.get_context('spawn')
        child_receiver,self.sender=ctx.Pipe(duplex=False)
        self.receiver,child_sender=ctx.Pipe(duplex=False)
        self.ipc_limit=min(4096,os.fpathconf(self.sender.fileno(),'PC_PIPE_BUF'))-4
        os.set_blocking(self.sender.fileno(),False)
        self.worker=ctx.Process(target=worker_target,args=(child_receiver,child_sender,descriptor,str(directory),self.mode,self._snapshot()),daemon=True)
        self.worker.start();child_receiver.close();child_sender.close()
        self.child_pid=self.worker.pid

    def _snapshot(self):
        return self.model.snapshot() if self.model else dict(self.booth_state)

    def _drain(self):
        try:
            while self.receiver.poll():
                message=self.receiver.recv()
                if message.get('ready'):self.ready=True;self.child_pid=message['pid']
                if message.get('error'):self._error=message['error']
                if message.get('ack'):self.inflight=False
        except (EOFError,OSError):
            if not self.closed:self._error=self._error or 'LCD worker connection closed'
        if not self.closed and not self.worker.is_alive():self._error=self._error or 'LCD worker exited'
        if not self.closed and not self.ready and time.monotonic()>self.startup_deadline:
            self._error=self._error or 'LCD worker initialization timed out'

    def _publish(self,force=False):
        if self.closed:return
        self._drain();now=time.monotonic()
        if self._error or not self.ready or self.inflight or (not force and now<self.next_publish):return
        state=self._snapshot()
        payload=zlib.compress(pickle.dumps(('paint',state),protocol=4),3)
        if len(payload)>self.ipc_limit:
            self._error='LCD snapshot exceeds bounded IPC frame';return
        try:
            self.sender.send_bytes(payload);self.inflight=True;self.next_publish=now+self.interval
        except BlockingIOError:pass  # <= PIPE_BUF: the entire frame remains unsent.
        except (BrokenPipeError,OSError):self._error='LCD worker unavailable'

    @property
    def error(self):
        self._drain();return self._error

    def update(self,**fields):
        if self.model:
            if all(name in fields for name in ('phase','verify','reason','stable','event','cycles')):self.model.pl(**fields)
            else:self.model.update(**fields)
        else:self.booth_state.update(fields)
        self._publish()

    def accepted(self,request,reply,at,service_us):
        if self.model:self.model.accepted(request,reply,at,service_us)
        elif request.magic==b'FPG1' and request.values[1]!=0xffffffff:
            self.booth_state.update(mask=reply.values[1],sequence=reply.sequence,session=reply.session,
                value=request.values[14]/1e6,build_id=reply.values[8],received_monotonic_ns=at,source='recorded_replay_with_j2_edit')
        self._publish()

    def host_claim(self,values,at,session=None):
        if self.model:self.model.host_claim(values,at,session)
        self._publish(force=True)

    def authority_claim(self,values,at,session):
        if self.model:self.model.authority_claim(values,at,session)
        self._publish(force=True)

    def rejected(self,reason,at):
        if self.model:self.model.rejected(reason,at)
        else:self.booth_state['last_rejection']=str(reason)
        self._publish()

    def close(self,wait=True):
        if self.closed:return
        self.closed=True
        try:
            if self.worker.is_alive():
                payload=zlib.compress(pickle.dumps(('close',self._snapshot()),protocol=4),3)
                if len(payload)<=self.ipc_limit:self.sender.send_bytes(payload)
        except (BrokenPipeError,OSError):pass
        self.worker.join(4 if wait else 0)
        if self.worker.is_alive():self.worker.terminate();self.worker.join(1)
        self.sender.close();self.receiver.close()
