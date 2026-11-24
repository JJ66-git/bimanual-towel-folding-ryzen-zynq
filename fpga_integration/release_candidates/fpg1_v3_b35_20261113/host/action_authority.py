"""Pre-dispatch FPG1 authority kernel, opt-in and qualification-gated.

The caller supplies a transport with a successfully probed FoldClient session and
an actuator callback. No permit is cached or reused. A rejection permanently
latches this instance. This is an application interlock, not a physical CAN gate.
"""
import hashlib
import math
import time
from collections import deque
from copy import deepcopy

from . import fold_protocol as wire
from .protocol import RUNNING, UNKNOWN, Sample, quantize
from .reference import evaluate, profile_crc


class AuthorityDenied(RuntimeError):
    def __init__(self,reason,record):
        super().__init__('FPGA authority denied: '+reason)
        self.reason=reason;self.record=record


class CheckedSender:
    def __init__(self,client,profile,*,reply_budget_us,feedback_limit_us,clock_ns=time.perf_counter_ns):
        if (type(reply_budget_us) is not int or reply_budget_us<=0
                or type(feedback_limit_us) is not int or feedback_limit_us<=0):
            raise ValueError('Explicit reply budget and feedback age limit are required')
        if client.sequence!=1:raise ValueError('A fresh successfully probed FPG1 session is required')
        self.client=client;self.profile=deepcopy(profile);self.crc=profile_crc(profile)
        self.reply_budget_us=reply_budget_us;self.clock_ns=clock_ns
        # B35's motion profile disables its stale-feedback violation. This host
        # check is additional; do not present it as a PL watchdog/interlock.
        self.feedback_limit_us=min(feedback_limit_us,profile.get('max_feedback_age_us') or feedback_limit_us)
        self.session=client.session;self.fault=None;self.last_step=-1
        self.records=deque(maxlen=2048);self.dispatched=0;self.last_reply=None

    def _deny(self,reason,record):
        if self.fault is None:self.fault=reason
        record.update(decision='DENY',reason=reason,dispatched=False)
        self.records.append(record)
        raise AuthorityDenied(reason,dict(record))

    def _stopped(self,callback,record):
        try:return bool(callback())
        except Exception:self._deny('stop_check_failed',record)

    def send(self,sample,target,dispatch,*,deadline_ns,stop_requested=lambda:False):
        """Check one immutable candidate, then call dispatch once, within its deadline.

        deadline_ns must use the same clock as clock_ns and include the caller's
        control-cycle budget. The original CPU checks must remain before this call.
        A timeout does not issue a replacement/hold/disable command automatically.
        """
        started=self.clock_ns()
        record=dict(session=self.session,started_ns=started,decision='PENDING',dispatched=False)
        if self.fault:self._deny('latched:'+self.fault,record)
        if type(deadline_ns) is not int:self._deny('invalid_deadline',record)
        deadline=min(deadline_ns,started+self.reply_budget_us*1000)
        record['deadline_ns']=deadline
        try:
            # Round-trip pack/unpack freezes caller-owned vectors too.
            sample=Sample.unpack(sample.pack())
            target=tuple(float(value) for value in target)
            if quantize(target)!=sample.target:self._deny('candidate_payload_mismatch',record)
        except AuthorityDenied:raise
        except Exception as error:self._deny('invalid_candidate:'+type(error).__name__,record)
        record.update(step=sample.step,request_sha256=hashlib.sha256(sample.pack()).hexdigest(),feedback_age_us=sample.feedback_age_us)
        if sample.profile_crc!=self.crc:self._deny('profile_mismatch',record)
        if sample.step==UNKNOWN or sample.step<=self.last_step or not sample.flags&RUNNING:
            self._deny('invalid_task_order',record)
        if sample.feedback_age_us==UNKNOWN:self._deny('unknown_feedback_age',record)
        if sample.feedback_age_us>self.feedback_limit_us:self._deny('feedback_expired_before_request',record)
        if self.client.session!=self.session:self._deny('session_changed',record)
        if self._stopped(stop_requested,record):self._deny('stop_requested',record)
        if self.clock_ns()>=deadline:self._deny('deadline_before_send',record)
        sequence=self.client.sequence;record['sequence']=sequence
        old_timeout=self.client.timeout
        try:
            self.client.timeout=min(old_timeout,(deadline-self.clock_ns())/1e9)
            if self.client.timeout<=0:self._deny('deadline_before_send',record)
            reply=self.client.send_sample(sample)
        except AuthorityDenied:raise
        except Exception as error:self._deny('transport:'+type(error).__name__,record)
        finally:self.client.timeout=old_timeout
        received=self.clock_ns();record.update(reply_received_ns=received,round_trip_us=(received-started)/1000)
        if reply is None:self._deny('no_matching_reply',record)
        if ((reply.magic,reply.version,reply.kind,reply.session,reply.sequence)!=
                (b'FPG1',3,2,self.session,sequence)):
            self._deny('reply_identity',record)
        if len(reply.values)!=10 or reply.values[0]!=self.crc or reply.values[8]!=wire.BUILD_ID:
            self._deny('reply_build_or_profile',record)
        self.last_reply=reply
        expected=evaluate(sample,self.profile)
        record.update(violations=reply.values[1],reference_match=(reply.values[1:4]==
            (expected['violations'],expected['events'],expected['phase'])),build_id=reply.values[8])
        if not record['reference_match']:self._deny('reference_mismatch',record)
        if reply.values[1]:self._deny('pl_violation',record)
        if self._stopped(stop_requested,record):self._deny('stop_requested',record)
        before_dispatch=self.clock_ns()
        if before_dispatch>=deadline:self._deny('reply_or_processing_late',record)
        if sample.feedback_age_us+math.ceil((before_dispatch-started)/1000)>self.feedback_limit_us:
            self._deny('feedback_expired_before_dispatch',record)
        # No callbacks/logging between the final deadline check and the sole send.
        self.last_step=sample.step
        record.update(decision='ALLOW',dispatch_started_ns=before_dispatch)
        try:result=dispatch(target)
        except Exception as error:
            if getattr(error,'command_sent',None) is False:
                self._deny(getattr(error,'reason','dispatch_not_started'),record)
            self.fault='dispatch_error';record.update(decision='DISPATCH_ERROR',reason=type(error).__name__,dispatch_outcome='unknown')
            self.records.append(record)
            raise
        self.dispatched+=1;record.update(dispatched=True,dispatch_returned_ns=self.clock_ns())
        self.records.append(record)
        return result
