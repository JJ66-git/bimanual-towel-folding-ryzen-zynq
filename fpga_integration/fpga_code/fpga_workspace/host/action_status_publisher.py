"""Coalescing display publisher: no socket waits in the policy thread."""
import socket
import threading
import time
from . import action_status as wire


class Publisher:
    def __init__(self,address,session,interval=.1):
        self.session=session;self.interval=interval;self.latest=None;self.delivered=None;self.error=None;self.sequence=0;self.acknowledged=0
        self.stop=threading.Event();self.wake=threading.Event();self.socket=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        self.socket.connect(address);self.socket.settimeout(.15)
        self.worker=threading.Thread(target=self._run,name='authority-display',daemon=True);self.worker.start()
    def publish(self,row):
        self.latest=dict(row);self.wake.set()
    def _run(self):
        last=None;tick=-1
        try:
            while not self.stop.is_set() or self.latest is not last:
                self.wake.wait(self.interval);self.wake.clear();row=self.latest
                if row is None or row is last:
                    if self.stop.is_set():break
                    continue
                tick=max(tick+1,time.perf_counter_ns()//1000)
                packet=wire.pack(1,self.session,self.sequence,tick,row)
                try:
                    self.socket.send(packet);reply=self.socket.recv(4096)
                    if wire.unpack(reply)!=(129,self.session,self.sequence,tick,row):raise ValueError('display_ack_mismatch')
                    self.delivered=dict(row);self.error=None;self.acknowledged+=1
                except (OSError,ValueError) as error:self.error=type(error).__name__+': '+str(error)
                self.sequence+=1;last=row
                if not self.stop.is_set():self.stop.wait(self.interval)
        except Exception as error:self.error=type(error).__name__+': '+str(error)
    def close(self):
        self.stop.set();self.wake.set();self.worker.join(2);self.socket.close()
