"""One guard writer, asynchronous LCD output, and B32 restoration on exit."""
import argparse
import copy
import json
import ipaddress
from pathlib import Path
import threading
import signal
import time

from board.pynq_udp_server import PynqGuard, serve
from board.ownership import exclusive_owner
from board.watchdog_monitor import Journal
from host.reference import load_profile
from screen.lcd import Framebuffer
from screen.view import ScreenState, render

ROOT=Path(__file__).resolve().parents[1]
COMBINED=ROOT/'overlay/lcd_framebuffer_20261101/lcd1_guard.bit'
BASELINE=ROOT/'overlay/own_hybrid_motion_guard_watchdog.bit'


class RuntimeJournal(Journal):
    def __init__(self,directory,source,display):
        super().__init__(directory/'board.jsonl')
        self.directory,self.source,self.display=directory,source,display
        self.state=ScreenState()
        self.lock=threading.RLock()
        self.stop=threading.Event()
        self.error=None
        self.frames=(directory/'frames.jsonl').open('x',encoding='utf-8',buffering=1)
        self.worker=threading.Thread(target=self.paint,name='lcd-render',daemon=True)
        self.worker.start()

    def write(self,event,**values):
        with self.lock:
            super().write(event,input_source=self.source,**values)
            self.state.apply(dict(event=event,board_monotonic_ns=time.monotonic_ns(),**values))

    def watchdog_sample(self,snapshot):
        with self.lock:
            if self.error is not None: raise RuntimeError('LCD worker failed: '+self.error)
            self.state.watchdog=snapshot.copy()

    def snapshot(self):
        with self.lock: return copy.deepcopy(self.state)

    @property
    def running(self):
        with self.lock:
            return self.state.watchdog is None or self.state.watchdog['state']!=4

    def paint(self):
        try:
            while not self.stop.is_set():
                started=time.perf_counter_ns()
                state=self.snapshot()
                image=render(state,'LIVE',self.source)
                rendered=time.perf_counter_ns()
                result=self.display.present(image)
                finished=time.perf_counter_ns()
                picture=self.directory/'latest.png.tmp'
                image.save(picture,format='PNG')
                picture.replace(self.directory/'latest.png')
                summary=dict(state.summary(),display_mode='LIVE',input_source=self.source,
                             physical_display_verified=False)
                temporary=self.directory/'latest.json.tmp'
                temporary.write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
                temporary.replace(self.directory/'latest.json')
                self.frames.write(json.dumps(dict(event='frame_presented',board_perf_ns=finished,
                    render_us=(rendered-started)/1000,present_us=(finished-rendered)/1000,
                    generation=result['generation'],checked=state.checked,
                    watchdog=state.watchdog,task_status=state.task))+'\n')
                self.stop.wait(max(0,0.5-(time.perf_counter_ns()-started)/1e9))
        except Exception as error:
            with self.lock: self.error=str(error)
            self.write('display_failed',error=str(error))

    def close(self):
        self.stop.set()
        self.worker.join()
        self.frames.close()
        final=dict(self.snapshot().summary(),display_mode='LIVE',display_error=self.error,
                   physical_display_verified=False)
        (self.directory/'final_state.json').write_text(json.dumps(final,indent=2)+'\n',encoding='utf-8')
        super().close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--peer',required=True,help='allowed sender IPv4')
    parser.add_argument('--bind',default='0.0.0.0')
    parser.add_argument('--port',type=int,default=9009)
    parser.add_argument('--source',required=True,choices=('synthetic_test','recorded_replay','host_observation'))
    parser.add_argument('--out',type=Path,required=True,help='new run directory')
    args=parser.parse_args()
    if not 0<args.port<65536: parser.error('invalid UDP port')
    try:ipaddress.IPv4Address(args.peer)
    except ValueError:parser.error('peer must be an IPv4 address')
    for path in (COMBINED,COMBINED.with_suffix('.hwh'),BASELINE,BASELINE.with_suffix('.hwh')):
        if not path.is_file(): parser.error('missing overlay: '+str(path))
    with exclusive_owner():
        args.out.mkdir(parents=True,exist_ok=False)
        run(args)


def run(args):
    from pynq import Overlay
    def stop(signum,frame):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    display=journal=None
    try:
        overlay=Overlay(str(COMBINED))
        guard=PynqGuard(overlay.guard)
        guard.write(0x4c,0)
        display=Framebuffer(overlay.lcd)
        journal=RuntimeJournal(args.out,args.source,display)
        serve(guard,load_profile(),journal,args.bind,args.port,args.peer)
    except KeyboardInterrupt:
        print('Stopped during initialization',flush=True)
    except Exception as error:
        (args.out/'failure.json').write_text(json.dumps(dict(error=str(error)))+'\n',encoding='utf-8')
        raise
    finally:
        try:
            if journal is not None: journal.close()
        finally:
            try:
                if display is not None: display.close()
            finally:
                restored=Overlay(str(BASELINE))
                guard=PynqGuard(restored.guard)
                guard.write(0x4c,0)
                record=dict(event='baseline_restored',build_id=guard.read(0),
                            watchdog_control=guard.read(0x4c),watchdog_status=guard.read(0x54))
                (args.out/'exit.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
                print(json.dumps(record),flush=True)


if __name__=='__main__': main()
