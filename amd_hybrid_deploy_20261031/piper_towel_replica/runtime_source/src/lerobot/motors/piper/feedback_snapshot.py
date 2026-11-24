"""Optional passive SDK parser observation; commands and limit handling stay in the SDK."""
import math
import threading
import time
from piper_sdk import C_PiperInterface_V2


def timestamped_interface(base):
    class MonotonicFeedback(base):
        def __init__(self,*args,**kwargs):
            if not hasattr(self,'feedback_snapshot_lock'):
                self.feedback_snapshot_lock=threading.RLock()
                self.feedback_arrivals={}
            super().__init__(*args,**kwargs)

        def ParseCANFrame(self,message):
            with self.feedback_snapshot_lock:
                cid=getattr(message,'arbitration_id',None)
                getter=(self.GetArmGripperMsgs if cid==0x2A8 else self.GetArmJointMsgs) if cid in (0x2A5,0x2A6,0x2A7,0x2A8) else None
                before=float(getter().time_stamp) if getter else None
                received=time.monotonic_ns()
                result=super().ParseCANFrame(message)
                if getter:
                    after=float(getter().time_stamp)
                    # An SDK-filtered frame must not refresh our freshness marker.
                    if math.isfinite(after) and after>0 and after!=before:
                        self.feedback_arrivals[cid]=(received,after)
                return result

        def feedback_monotonic_snapshot(self):
            with self.feedback_snapshot_lock:
                joints=[self.feedback_arrivals.get(cid) for cid in (0x2A5,0x2A6,0x2A7)]
                gripper=self.feedback_arrivals.get(0x2A8)
                return dict(source='sdk_monotonic_atomic',
                    joint_monotonic_ns=min(item[0] for item in joints) if all(joints) else None,
                    gripper_monotonic_ns=gripper[0] if gripper else None)
    return MonotonicFeedback


PiperFeedbackInterface=timestamped_interface(C_PiperInterface_V2)
