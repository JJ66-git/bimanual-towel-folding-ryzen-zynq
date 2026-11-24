"""Best-effort native reset lifecycle; display thread never calls robot APIs."""
import json
import os
from pathlib import Path
import sys
import threading

WORKSPACE=Path(__file__).resolve().parents[3]/'fpga_integration/fpga_code/fpga_workspace'
sys.path.insert(0,str(WORKSPACE))
from host.task_status import Client


class ResetDisplay:
    def __init__(self,address,*,client_factory=Client):
        self.address=address;self.client_factory=client_factory;self.phase='RESETTING'
        self.done=threading.Event();self.receipts=[];self.error=None
        self.thread=threading.Thread(target=self._worker,name='reset-display',daemon=True)
        self.thread.start()

    @classmethod
    def from_environment(cls):
        address=os.environ.get('AIPC_FPGA_FOLD')
        if not address:return None
        try:
            host,port=address.rsplit(':',1)
            return cls((host,int(port)))
        except Exception as error:
            print('RESET_DISPLAY_UNAVAILABLE='+type(error).__name__,flush=True)
            return None

    def _worker(self):
        client=None
        try:
            client=self.client_factory(self.address,timeout=.25)
            first=True
            while True:
                phase='RESETTING' if first else self.phase;first=False
                try:
                    self.receipts.append(client.send(phase,'EXECUTING' if phase=='RESETTING' else 'ERROR' if phase=='RESET_FAILED' else 'IDLE','HOST'))
                    self.receipts=self.receipts[-32:]
                    self.error=None
                except Exception as error:self.error=type(error).__name__+': '+str(error)
                if phase!='RESETTING':break
                self.done.wait(.8)
        except Exception as error:self.error=type(error).__name__+': '+str(error)
        finally:
            if client:client.close()

    def close(self,outcome):
        self.phase={'ended':'RESET_DONE','stopped':'RESET_STOPPED','error':'RESET_FAILED'}[outcome]
        self.done.set();self.thread.join(timeout=1.5)
        print('RESET_DISPLAY='+json.dumps(dict(phase=self.phase,receipts=self.receipts,error=self.error,
            thread_finished=not self.thread.is_alive(),motion_permission=False,physical_result_verified=False)),flush=True)
