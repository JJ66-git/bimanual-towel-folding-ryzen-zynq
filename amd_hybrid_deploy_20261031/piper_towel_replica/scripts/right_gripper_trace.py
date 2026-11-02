"""Observe the unchanged policy entry: right GripperCtrl -> native 0x159 -> raw 0x2A8.

No extra CAN socket/thread/send, no enable/clear/zero/retry decisions. Buffer in
memory and export after original cleanup. Native send success is NOT an ACK.
"""
import argparse
from collections import Counter, deque
from contextlib import contextmanager
import inspect
import json
from pathlib import Path
import runpy
import sys
from threading import Lock, local
import time

ROOT = Path(__file__).resolve().parents[1]


def decode_frame(row):
    """Decode saved bytes only; keep signed raw feedback before SDK limits."""
    result = dict(row)
    if row.get('kind') not in ('tx_begin','rx'):
        return result
    data = bytes.fromhex(row['data_hex'])
    if len(data) != 8:
        return dict(result,decode_error='Expected 8-byte gripper frame')
    width = int.from_bytes(data[:4],'big',signed=True)
    result.update(width_um=width,width_mm=width/1000.)
    if row['kind']=='tx_begin':
        result.update(effort_raw=int.from_bytes(data[4:6],'big'),gripper_code=data[6],set_zero=data[7])
    else:
        result.update(effort_raw=int.from_bytes(data[4:6],'big',signed=True),
                      status_code=data[6],enabled=bool(data[6]&0x40),
                      fault_bits=data[6]&0x3F,zeroed=bool(data[6]&0x80))
    return result


class GripperTrace:
    def __init__(self,right_can):
        self.right_can = right_can
        self.lock,self.context = Lock(),local()
        self.pre_rx,self.events = deque(maxlen=2048),[]
        self.started_ns,self.next_id = None,0
        self.frozen,self.overflow,self.recording_error = False,0,None
        self.anchor = dict(wall_time_ns=time.time_ns(),monotonic_ns=time.perf_counter_ns())

    def record(self,kind,**fields):
        try:
            row = dict(kind=kind,monotonic_ns=time.perf_counter_ns(),channel=self.right_can,**fields)
            with self.lock:
                if self.frozen:
                    return None
                if kind in ('api_begin','tx_begin'):
                    self.next_id += 1
                    row['event_id'] = self.next_id
                    if self.started_ns is None:
                        self.started_ns = row['monotonic_ns']
                        self.events.extend(self.pre_rx)
                        self.pre_rx.clear()
                elif kind=='rx' and self.started_ns is None:
                    self.pre_rx.append(row)
                    return None
                if len(self.events)>=50_000:
                    self.overflow += 1
                    return None
                self.events.append(row)
                return row.get('event_id')
        except Exception as error:
            self.recording_error = type(error).__name__
            return None

    @contextmanager
    def observe(self):
        from piper_sdk import C_PiperInterface_V2 as Piper
        from piper_sdk.hardware_port import C_STD_CAN as Transport
        original_api,original_send,original_parse = Piper.GripperCtrl,Transport.SendCanMessage,Piper.ParseCANFrame
        signature = inspect.signature(original_api)
        recorder = self

        def is_right(piper):
            try:
                return piper.GetCanBus().GetCanPortName()==recorder.right_can
            except Exception as error:
                recorder.recording_error = type(error).__name__
                return False

        def gripper(piper,*args,**kwargs):
            if not is_right(piper):
                return original_api(piper,*args,**kwargs)
            api_id,old_id = None,getattr(recorder.context,'api_id',None)
            try:
                bound = signature.bind(piper,*args,**kwargs)
                bound.apply_defaults()
                requested = {k:v for k,v in bound.arguments.items() if k!='self'}
                api_id = recorder.record('api_begin',api='GripperCtrl',requested=requested)
                recorder.context.api_id = api_id
            except Exception as error:
                recorder.recording_error = type(error).__name__
            try:
                result = original_api(piper,*args,**kwargs)
            except BaseException as error:
                recorder.record('api_end',api_id=api_id,exception=type(error).__name__)
                raise
            else:
                recorder.record('api_end',api_id=api_id,returned=result)
                return result
            finally:
                recorder.context.api_id = old_id

        def send(bus,arbitration_id,data,*args,**kwargs):
            selected,tx_id = False,None
            try:
                selected = arbitration_id==0x159 and bus.GetCanPortName()==recorder.right_can
                if selected:
                    tx_id = recorder.record('tx_begin',api_id=getattr(recorder.context,'api_id',None),
                        can_id=arbitration_id,data_hex=bytes(data).hex(),
                        dlc=(args[0] if args else kwargs.get('dlc',8)),
                        is_extended_id=(args[1] if len(args)>1 else kwargs.get('is_extended_id',False)))
            except Exception as error:
                recorder.recording_error = type(error).__name__
            try:
                result = original_send(bus,arbitration_id,data,*args,**kwargs)
            except BaseException as error:
                if selected:
                    recorder.record('tx_end',tx_id=tx_id,exception=type(error).__name__)
                raise
            if selected:
                try:
                    recorder.record('tx_end',tx_id=tx_id,return_status=result.name)
                except Exception as error:
                    recorder.recording_error = type(error).__name__
            return result

        def parse(piper,frame):
            entered,fields = time.perf_counter_ns(),None
            try:
                if frame.arbitration_id==0x2A8 and is_right(piper):
                    fields = dict(can_id=frame.arbitration_id,data_hex=bytes(frame.data).hex(),
                                  can_timestamp_s=frame.timestamp,is_error_frame=frame.is_error_frame)
            except Exception as error:
                recorder.recording_error = type(error).__name__
            try:
                result = original_parse(piper,frame)
            except BaseException as error:
                if fields is not None:
                    recorder.record('rx_parse_error',entered_monotonic_ns=entered,exception=type(error).__name__,**fields)
                raise
            if fields is not None:
                recorder.record('rx',entered_monotonic_ns=entered,**fields)
            return result

        Piper.GripperCtrl,Transport.SendCanMessage,Piper.ParseCANFrame = gripper,send,parse
        try:
            yield
        finally:
            with self.lock:
                self.frozen = True
            Piper.GripperCtrl,Transport.SendCanMessage,Piper.ParseCANFrame = original_api,original_send,original_parse

    def save(self,directory,arguments,original_error,namespace):
        rows = self.events+(list(self.pre_rx) if self.started_ns is None else [])
        rows = [decode_frame(r) for r in sorted(rows,key=lambda r:r.get('entered_monotonic_ns',r['monotonic_ns']))]
        with (directory/'timeline.jsonl').open('w',encoding='utf-8') as output:
            for row in rows:
                output.write(json.dumps(row,allow_nan=False)+'\n')
        summary = dict(schema='right-gripper-txrx-v1',status='ORIGINAL_ERROR' if original_error else 'COMPLETE',
            original_error=original_error,original_arguments=arguments,execute='--execute' in arguments,
            right_can=self.right_can,entry=str(ROOT/'deployment_entry/cobot_hybrid.py'),
            stopped_by_original_handler=namespace.get('stop'),
            time_anchor=self.anchor,first_gripper_api_or_tx_ns=self.started_ns,
            event_counts=dict(Counter(r['kind'] for r in rows)),
            recording_error=self.recording_error,overflow_events=self.overflow,
            policy_trace=str(directory/'policy_trace.json'),
            pre_command_rx_scope='Latest 2048 right gripper frames only; no time-window truncation after first gripper call',
            scope='Requested native GripperCtrl + encoded SendCanMessage call/result + raw RX before SDK limits; NOT wire/controller ACK',
            changes='Observation only: no extra commands, enable/clear/zero/retry, model/filter/limit or control-order changes')
        (directory/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False),encoding='utf-8')
        print('RIGHT_GRIPPER_TRACE='+json.dumps(dict(summary=str(directory/'summary.json'),
            timeline=str(directory/'timeline.jsonl'),policy_trace=str(directory/'policy_trace.json'),
            status=summary['status'],recording_error=self.recording_error,overflow_events=self.overflow)),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir',type=Path,required=True)
    parser.add_argument('--right-can',required=True)
    parser.add_argument('policy_args',nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.policy_args[1:] if args.policy_args[:1]==['--'] else args.policy_args
    args.out_dir.mkdir(parents=True,exist_ok=False)
    (args.out_dir/'summary.json').write_text(json.dumps(dict(status='RECORDING')),encoding='utf-8')
    recorder,original_error,namespace = GripperTrace(args.right_can),None,{}
    old_argv,old_path = sys.argv,sys.path.copy()
    try:
        entry = ROOT/'deployment_entry/cobot_hybrid.py'
        sys.argv = [str(entry),*arguments]
        sys.path[:] = [str(entry.parent),*old_path[1:]]
        with recorder.observe():
            namespace = runpy.run_path(str(entry),run_name='__main__')
    except SystemExit as error:
        if error.code not in (None,0):
            original_error = f'SystemExit: {error.code}'
        raise
    except BaseException as error:
        original_error = f'{type(error).__name__}: {error}'
        raise
    finally:
        sys.argv,sys.path[:] = old_argv,old_path
        try:
            recorder.save(args.out_dir,arguments,original_error,namespace)
        except Exception as error:
            print('RIGHT_GRIPPER_TRACE_WRITE_ERROR='+str(error)+'; folder='+str(args.out_dir),file=sys.stderr,flush=True)


if __name__=='__main__':
    main()
