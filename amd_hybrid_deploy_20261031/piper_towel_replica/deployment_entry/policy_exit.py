"""Native measured-position exit with explicit, bounded host-send evidence.

No cached candidate, enable, clear, reset or zero command. This is the existing
native hold scope, outside the latched strategy gate. A return is not a physical
hold acknowledgement. No camera read is allowed on this exit path.
"""
import time
from fpga_feedback_age import FeedbackAgeClock


def hold_current(wrapper,positions,action_dict,driver_reader,*,budget_us=20000,feedback_limit_us=100000,
                 clock_ns=time.perf_counter_ns,age_clock=None):
    record=dict(kind='native_measured_exit_hold',command_started=False,command_returned=False,
                physical_hold_verified=False,fpga_gated=False,started_ns=clock_ns())
    try:
        deadline=record['started_ns']+budget_us*1000
        observation,timestamps,sampled_ns=wrapper.get_feedback_snapshot(timeout=budget_us/1e6)
        age=(age_clock or FeedbackAgeClock()).age_us(timestamps)
        record.update(feedback_age_us=age,feedback_sampled_ns=sampled_ns)
        if age>feedback_limit_us:raise ValueError('Exit feedback unavailable or stale; no replacement target sent')
        drivers=driver_reader();record['driver_before']=drivers
        for side in ('left','right'):
            row=drivers[side]
            if (row.get('joint_enabled')!=[True]*6 or row.get('enabled') is not True
                    or row.get('fault_bits')!='0x0'):
                raise ValueError('Exit drivers disabled/faulted; no automatic enable or gripper recovery')
        target=positions(observation);record['target']=[float(v) for v in target]
        record['command_started']=True
        try:wrapper.send_action_before(action_dict(target),deadline)
        except Exception as error:
            if getattr(error,'command_sent',None) is False:record['command_started']=False
            raise
        record.update(command_returned=True,returned_ns=clock_ns(),driver_after=driver_reader())
    except Exception as error:record['error']=type(error).__name__+': '+str(error)
    return record
