#!/usr/bin/env python3
"""Exercise the real Tk window with simulation only; capture its own bounds."""
from dataclasses import replace
import json
from pathlib import Path
from queue import Queue, Empty, Full
import statistics
import sys
import threading
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'amd_hybrid_deploy_20261031'))
from shared_runtime import dashboard_view as view
from shared_runtime.tk_support import load_tk
from demo_live_dashboard import demo_state


def main():
    from shared_runtime.dashboard_capture import capture as capture_window
    output=ROOT/'npu_deploy/artifacts/dashboard_ui'
    output.mkdir(parents=True,exist_ok=True)
    tk=load_tk();original_tk=tk.Tk;original_render=view.render_dashboard
    stopping=threading.Event();frames=Queue(maxsize=1);durations=[];captures=[]
    stale_control=threading.Event()
    interaction_checks=[]
    class Ready:
        def send(self,value):
            if not value[0]:raise RuntimeError(value)
        def close(self):pass
    def timed(*args,**kwargs):
        started=time.perf_counter();result=original_render(*args,**kwargs)
        durations.append((time.perf_counter()-started)*1000);return result
    def root_factory():
        root=original_tk()
        def capture(name):
            root.update_idletasks()
            capture_window(root).save(output/(name+'.png'))
            captures.append(dict(name=name,width=root.winfo_width(),height=root.winfo_height()))
        def toggle():
            next(widget for widget in root.winfo_children() if isinstance(widget,tk.Button)).invoke()
        root.after(10000,lambda:capture('tk-wide'))
        root.after(11000,toggle)
        root.after(12000,lambda:capture('tk-details'))
        root.after(13000,lambda:root.geometry('900x640'))
        root.after(14000,lambda:capture('tk-narrow'))
        root.after(14500,lambda:root.event_generate('<KeyPress-d>'))
        root.after(15000,stale_control.set)
        root.after(17000,lambda:capture('tk-stale'))
        def press(label):
            next(widget for widget in root.winfo_children() if isinstance(widget,tk.Button) and widget.cget('text')==label).invoke()
        frozen_sequence=[]
        def copy_button():
            next(widget for widget in root.winfo_children() if isinstance(widget,tk.Button) and widget.cget('text') in ('复制诊断','已复制')).invoke()
        def begin_freeze():
            press('冻结显示');copy_button()
            frozen_sequence.append(json.loads(root.clipboard_get())['board']['sequence'])
        def check_copy():
            copy_button()
            payload=json.loads(root.clipboard_get())
            assert payload['display_frozen'] and payload['board']['session']==0x27A14C82
            assert payload['board']['sequence']==frozen_sequence[0]
            assert payload['board']['sequence']+2<step_count[0]
            interaction_checks.append('Frozen diagnostic copied with the matching session/sequence; live feed continued')
        root.after(17500,stale_control.clear)
        root.after(18000,lambda:press('FPGA 协同'))
        root.after(19000,lambda:capture('tk-fpga-narrow'))
        root.after(20000,begin_freeze)
        root.after(21000,check_copy)
        root.after(22000,lambda:capture('tk-fpga-frozen'))
        root.after(23000,lambda:press('继续实时'))
        root.after(24000,lambda:root.geometry('1520x940'))
        root.after(25000,lambda:capture('tk-fpga-wide'))
        root.after(26000,lambda:root.event_generate('<Button-5>'))
        root.after(27000,lambda:capture('tk-fpga-scrolled'))
        root.after(28000,lambda:press('实时画面'))
        root.after(29000,lambda:capture('tk-return-camera'))
        root.after(30000,stopping.set)
        return root
    step_count=[0]
    def feed():
        started=time.monotonic();step=0
        while not stopping.is_set():
            now=time.monotonic();state=demo_state('running',now)
            value=replace(state.control,step=step,dispatched_steps=step+1,corrected_steps=step+1,
                authority=.45+.35*__import__('math').sin((now-started)*.7),elapsed_s=now-started)
            board_state=demo_state('board-ok',now)
            board=replace(board_state.board,sequence=step,step=max(0,step-2),checked_count=step+1,reference_matches=step+1)
            board_history=tuple(replace(board,sequence=i,step=max(0,i-2)) for i in range(max(0,step-31),step+1))
            packet=dict(board=board,board_history=board_history,board_link=board_state.board_link,sampled=now,images=state.images,joints=state.joints,
                feedback_times=state.feedback_times,poses=state.poses,
                control=None if stale_control.is_set() else value)
            try:frames.put_nowait(packet)
            except Full:pass
            step+=1;step_count[0]=step;stopping.wait(.2)
    worker=threading.Thread(target=feed,daemon=True);worker.start()
    with patch.object(tk,'Tk',root_factory),patch.object(view,'render_dashboard',side_effect=timed):
        view.window(frames,stopping,Ready(),'FlexiFold · UI verification / SIMULATION','SIMULATION')
    worker.join(timeout=1)
    report=dict(captures=captures,interaction_checks=interaction_checks,render_samples=len(durations),
        render_median_ms=statistics.median(durations),
        render_p95_ms=sorted(durations)[int(.95*(len(durations)-1))],
        verification='Tk painting, native Canvas curve, parameter button, keyboard shortcut, resize, telemetry freshness; simulation only')
    (output/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    if len(captures)!=9 or len(interaction_checks)!=1:raise RuntimeError('Incomplete UI verification')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
