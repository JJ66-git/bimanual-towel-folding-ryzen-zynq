"""Bounded, read-only B35 observer. All UDP/vision/logging runs off the control tick."""
from __future__ import annotations
from dataclasses import replace
import ipaddress
import json
import math
import os
import queue
import sys
import threading
import time
from pathlib import Path
import numpy as np


def _camera_ready_us(timestamp, *, perf_now, monotonic_ns):
    return int(timestamp * 1_000_000) + monotonic_ns // 1000 - int(perf_now * 1_000_000)


class FpgaFoldObserver:
    def __init__(self,address,camera,quality_profile=None,*,queue_capacity=8):
        root=Path(__file__).resolve().parents[3]
        workspace=root/'fpga_integration/fpga_code/fpga_workspace'
        sys.path.insert(0,str(workspace))
        from host import fold_protocol as wire
        from host.fold_client import FoldClient
        from host.protocol import CHECK_CORRECTION,RUNNING,UNKNOWN,Sample,quantize
        from host.reference import load_profile,profile_crc,evaluate
        from towel_quality import cloth_features
        self.wire,self.Sample=wire,Sample
        self.CHECK_CORRECTION,self.RUNNING,self.UNKNOWN=CHECK_CORRECTION,RUNNING,UNKNOWN
        self.quantize,self.cloth_features,self.evaluate=quantize,cloth_features,evaluate
        self.guard_profile=load_profile(workspace/'config/guard_profile.json')
        self.guard_crc=profile_crc(self.guard_profile)
        self.quality=json.loads(Path(quality_profile).read_text()) if quality_profile else None
        if self.quality is not None and self.quality.get('schema')!='blue_towel_quality_v1':
            raise ValueError('Unsupported towel feature profile')
        self.input_source=os.environ.get('AIPC_FPGA_SOURCE','HOST_OBSERVATION')
        self.calibration_status=(self.quality or {}).get('calibration_status','unconfigured')
        self.vision_enabled=self.quality is not None
        self.camera,self.address=camera,address
        self._client_factory=lambda:FoldClient(address,self.guard_crc)
        self.client=self._client_factory()
        self.queue=queue.Queue(maxsize=queue_capacity)
        self.stop_event=threading.Event();self.ready=threading.Event()
        self.worker=threading.Thread(target=self._run,name='fpga-fold-observer',daemon=True)
        self.errors=0;self.last_error=None;self.dropped=0
        self._frame_id=0;self._last_camera_timestamp=None;self._event_rx_by_seq={}
        self._baseline_area=None;self._probe_sample=None;self._started=False
        self._channel=None;self._run_id=None
        try:
            from shared_runtime.dashboard_telemetry import channel
            self._channel=channel()
            self._run_id=getattr(self._channel.control,'run_id',None) if self._channel else None
        except ImportError:pass
        self.output=root/'npu_deploy/artifacts/fpga_fold'/f'run_{time.time_ns()}_{os.getpid()}'
        self.output.mkdir(parents=True,exist_ok=False)
        self.log=(self.output/'observer.jsonl').open('x',encoding='utf-8',buffering=1)
        self._log('observer_created',address=list(address),protocol='FPG1_v3/FVE1_v1',
                  build_id=wire.BUILD_ID,calibration_status=self.calibration_status,
                  vision_enabled=self.vision_enabled,source=self.input_source,
                  scope='read-only observer; never authorizes motion or task success')

    @classmethod
    def from_environment(cls,camera,quality_profile=None):
        value=os.environ.get('AIPC_FPGA_FOLD')
        if not value:return None
        host,sep,port=value.rpartition(':')
        if not sep or not 1<=int(port)<=65535:raise ValueError('AIPC_FPGA_FOLD must be IPv4:port')
        return cls((str(ipaddress.IPv4Address(host)),int(port)),camera,quality_profile)

    def _log(self,event,**fields):
        record=dict(fields)
        record.setdefault('session',self.client.session)
        record.update(event=event,monotonic_ns=time.monotonic_ns())
        self.log.write(json.dumps(record,allow_nan=False)+'\n')

    def start(self,start_pose,baseline_area=None):
        if self._started:raise RuntimeError('Observer already started')
        self._baseline_area=float(baseline_area) if baseline_area is not None else None
        if self.vision_enabled and (self._baseline_area is None or not math.isfinite(self._baseline_area) or self._baseline_area<=0):
            raise ValueError('Visual features require a measured positive start-frame area')
        zeros=(0,)*14;start=tuple(int(x) for x in start_pose)
        self._probe_sample=self.Sample(self.guard_crc,self.UNKNOWN,0,0,self.UNKNOWN,self.UNKNOWN,
                                       start,start,start,start,zeros,zeros)
        self._started=True
        self.worker.start()  # Publish the probe before allowing the worker to run.

    def wait_ready(self,timeout=8):
        if not self.ready.wait(timeout):raise TimeoutError(self.last_error or 'No matched B35 startup probe')

    def submit(self,*,step,elapsed_s,target,measured,previous,act,correction,
               previous_correction,blended,feedback_age_us,queue_depth,dispatched=False):
        try:
            flags=self.RUNNING|(self.CHECK_CORRECTION if blended else 0)
            sample=self.Sample(self.guard_crc,int(step),flags,max(0,int(elapsed_s*1_000_000)),
                 int(feedback_age_us),int(queue_depth),*(self.quantize(v) for v in
                 (target,measured,previous,act,correction,previous_correction)))
            self._put(('sample',sample,time.monotonic_ns(),bool(dispatched)))
        except Exception as exc:self._record_error('sample',exc)

    def _put(self,item):
        try:self.queue.put_nowait(item)
        except queue.Full:
            try:self.queue.get_nowait();self.dropped=getattr(self,'dropped',0)+1
            except queue.Empty:pass
            try:self.queue.put_nowait(item)
            except queue.Full:pass

    def _record_error(self,stage,error):
        self.errors+=1;self.last_error=f'{stage}: {type(error).__name__}: {error}'
        if hasattr(self,'log'):self._log('observer_error',stage=stage,error=str(error))
        if self.errors==1:print('FPGA_FOLD_OBSERVER_ERROR='+self.last_error,flush=True)

    def _camera_snapshot(self):
        lock=getattr(self.camera,'frame_lock',None)
        if lock is None:raise RuntimeError('Camera has no atomic frame/timestamp lock')
        with lock:
            frame=self.camera.latest_frame;timestamp=self.camera.latest_timestamp
            if frame is None or timestamp is None:return None
            if timestamp==self._last_camera_timestamp:return None
            image=frame.copy()
            frame_id=getattr(self.camera,'latest_frame_id',None)
            producer_ready=getattr(self.camera,'latest_ready_monotonic_us',None)
        self._last_camera_timestamp=timestamp
        if frame_id is None:
            if self._frame_id>=0xFFFFFFFE:raise RuntimeError('Camera frame id exhausted')
            self._frame_id+=1;frame_id=self._frame_id
        ready_us=int(producer_ready) if producer_ready is not None else _camera_ready_us(float(timestamp),perf_now=time.perf_counter(),monotonic_ns=time.monotonic_ns())
        return frame_id,ready_us,float(timestamp),image

    def _verify(self,event_sequence,event_rx_us):
        frame=self._camera_snapshot()
        if frame is None:return False
        frame_id,ready_us,_,rgb=frame
        if ready_us<=event_rx_us:return False
        feature=self.cloth_features(rgb,self.quality['segmentation'])
        # The delivered overlay has uncalibrated shape ranges. Never claim a field
        # confirmation from an unvalidated profile, even when blue pixels are found.
        if (getattr(self,'calibration_status','synthetic_test')!='field_validated'
                and not (getattr(self,'calibration_status','synthetic_test')=='synthetic_test'
                         and getattr(self,'input_source','SYNTHETIC') in ('SYNTHETIC','REPLAY'))):
            feature=None
        response=self.client.send_feature(event_sequence,event_rx_us,frame_id,ready_us,feature,self._baseline_area)
        if hasattr(self,'log'):
            self._log('vision_result',event_sequence=event_sequence,frame_id=frame_id,
                      frame_ready_us=ready_us,event_rx_us=event_rx_us,
                      values=list(response.values) if response else None)
        if response is not None:self._publish_vision(event_sequence,response)
        return response is not None and response.values[1] in (2,3)

    def _publish_vision(self,event_sequence,response):
        try:
            from shared_runtime.dashboard_telemetry import publish_vision
            publish_vision(self.client.session,event_sequence,response.values,self._channel,self._run_id)
        except ImportError:pass

    def _publish_sample(self,sample,response,dispatched,rtt_us):
        values=response.values;expected=self.evaluate(sample,self.guard_profile)
        record=dict(kind='task_sample',dispatched=dispatched,session=response.session,sequence=response.sequence,
            step=sample.step,reference_match=(values[1]==expected['violations'] and values[2]==expected['events'] and values[3]==expected['phase']),
            violations=values[1],events=values[2],phase=values[3],board_processing_us=values[4],
            host_round_trip_us=rtt_us,protocol_version=3,phase_state=values[5],phase_flags=values[6],
            phase_cycles=values[7],build_id=values[8],event_seq=values[9],verify_state=(values[6]>>8)&3,
            calibration_status=self.calibration_status,feedback_age_us=sample.feedback_age_us)
        self._log('task_reply',**record)
        try:
            from shared_runtime.dashboard_telemetry import publish_board
            publish_board(record,self._channel,self._run_id)
        except ImportError:pass

    def _run(self):
        pending={}
        try:
            probe_deadline=time.monotonic()+7.5
            while True:
                try:
                    probe=self.client.probe(self._probe_sample)
                    break
                except TimeoutError:
                    self.client.close()
                    if self.stop_event.is_set() or time.monotonic()>=probe_deadline:raise
                    self.client=self._client_factory()
                    self.stop_event.wait(.1)
            self._log('probe_accepted',values=list(probe.values));self.ready.set()
            print(f'FPGA_FOLD_PROBE=accepted BUILD_ID=0x{self.wire.BUILD_ID:08x}',flush=True)
            try:
                from shared_runtime.dashboard_telemetry import publish_link
                publish_link(dict(address=self.address,profile_crc=f'{self.guard_crc:08x}',controller_budget=750,
                    fpga_budget=self.guard_profile['max_actions'],protocol_version=3,build_id=self.wire.BUILD_ID),
                    self.client.session,self._channel,self._run_id)
            except ImportError:pass
            while not self.stop_event.is_set():
                try:item=self.queue.get(timeout=.01)
                except queue.Empty:item=None
                if item is not None:
                    _,sample,queued_ns,dispatched=item
                    delay_us=max(0,(time.monotonic_ns()-queued_ns+999)//1000)
                    if sample.feedback_age_us!=self.UNKNOWN:
                        sample=replace(sample,feedback_age_us=min(0xFFFFFFFE,sample.feedback_age_us+delay_us))
                    started=time.monotonic_ns();response=self.client.send_sample(sample)
                    if response is not None:
                        self._publish_sample(sample,response,dispatched,(time.monotonic_ns()-started)//1000)
                        event=response.values[9]
                        if event!=self.UNKNOWN and event not in self._event_rx_by_seq:
                            rx=time.monotonic_ns()//1000;self._event_rx_by_seq[event]=rx
                            pending[event]=(rx,time.monotonic()+1.5)
                for event,(rx,deadline) in list(pending.items()):
                    try:
                        if time.monotonic()>=deadline:
                            result=self.client.query_event(event)
                            if result is not None:self._publish_vision(event,result)
                            self._log('event_deadline_query',event_sequence=event,values=list(result.values) if result else None)
                            del pending[event]
                        elif self.vision_enabled and self._verify(event,rx):del pending[event]
                    except Exception as exc:
                        self._record_error('vision',exc);del pending[event]
        except Exception as exc:self._record_error('worker',exc)
        finally:
            self.client.close()
            self._log('observer_stopped',errors=self.errors,dropped=self.dropped)
            self.log.close()

    def close(self,timeout=2,*,outcome='stopped'):
        self.stop_event.set()
        if self.worker.is_alive():self.worker.join(timeout)
        if self.worker.is_alive():
            self.client.close();self.worker.join(.5)
        if not self._started:
            self.client.close();self.log.close()
        if self.ready.is_set():
            phase={'ended':'ENDED','error':'FAILED','stopped':'STOPPED'}[outcome]
            receipt=dict(address=list(self.address),session=self.client.session,
                         phase=phase,origin='HOST',task_result='unconfirmed',delivered=False)
            status=None
            try:
                from host.task_status import Client
                status=Client(self.address,timeout=.8,session=self.client.session)
                status.send(phase,'ERROR' if outcome=='error' else 'IDLE','HOST')
                receipt['delivered']=True
            except Exception as error:receipt['error']=str(error)
            finally:
                if status is not None:status.close()
            print('FPGA_TASK_END='+json.dumps(receipt),flush=True)
