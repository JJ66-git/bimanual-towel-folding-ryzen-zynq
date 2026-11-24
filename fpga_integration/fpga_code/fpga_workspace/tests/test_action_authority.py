"""Fault-injected authority tests; the actuator is an in-memory list only."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from host.action_authority import CheckedSender,AuthorityDenied
from host import fold_protocol as wire
from host.protocol import RUNNING,UNKNOWN,Sample
from host.reference import load_profile,profile_crc,evaluate
from host.fold_client import FoldClient
from board.fold_bridge import FoldSession


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.now=1_000_000_000;self.profile=load_profile();pose=tuple(self.profile['start']);zeros=(0,)*14
        self.sample=Sample(profile_crc(self.profile),0,RUNNING,1,1000,0,pose,pose,pose,pose,zeros,zeros)
        self.sent=[];self.requests=[];self.delay_us=1000;self.mutate=lambda reply:reply
        self.client=SimpleNamespace(session=123,sequence=1,timeout=.2,send_sample=self.exchange)
        self.gate=CheckedSender(self.client,self.profile,reply_budget_us=20_000,feedback_limit_us=100_000,clock_ns=lambda:self.now)
    def exchange(self,sample):
        self.requests.append(sample);self.now+=self.delay_us*1000
        ref=evaluate(sample,self.profile);seq=self.client.sequence;self.client.sequence+=1
        return self.mutate(wire.Frame(b'FPG1',3,2,123,seq,99,
            (profile_crc(self.profile),ref['violations'],ref['events'],ref['phase'],2000,0,4,15,wire.BUILD_ID,UNKNOWN)))
    def send(self,sample=None,**kwargs):
        sample=sample or self.sample
        return self.gate.send(sample,tuple(v/1e6 for v in sample.target),self.sent.append,
                              deadline_ns=self.now+30_000_000,**kwargs)
    def denied(self,reason,sample=None,**kwargs):
        with self.assertRaises(AuthorityDenied) as error:self.send(sample,**kwargs)
        self.assertEqual(error.exception.reason,reason);self.assertEqual(self.sent,[])
        self.assertIsNotNone(self.gate.fault)
    def test_matching_pl_reply_precedes_the_only_unchanged_dispatch(self):
        self.send();self.assertEqual(self.sent,[tuple(v/1e6 for v in self.sample.target)])
        row=self.gate.records[-1];self.assertLessEqual(row['reply_received_ns'],row['dispatch_started_ns'])
        self.assertTrue(row['dispatched']);self.assertEqual(self.client.timeout,.2)
    def test_pl_rejection_prevents_dispatch_and_latches(self):
        target=list(self.sample.target);target[8]=2650000
        bad=replace(self.sample,target=tuple(target))
        self.denied('pl_violation',bad)
        self.assertEqual(self.gate.records[-1]['violations']&1,1)
        self.denied('latched:pl_violation');self.assertEqual(len(self.requests),1)
    def test_no_reply_is_not_replaced_by_cpu_reference(self):
        self.mutate=lambda _:None;self.denied('no_matching_reply')
    def test_late_pass_is_not_permission(self):
        self.delay_us=20_001;self.denied('reply_or_processing_late')
    def test_old_session_sequence_and_wrong_build_are_rejected(self):
        for field,value,reason in [('session',999,'reply_identity'),('sequence',0,'reply_identity'),('build_id',0x46504734,'reply_build_or_profile')]:
            with self.subTest(field=field):
                self.setUp()
                def mutate(reply):
                    if field=='build_id':
                        values=list(reply.values);values[8]=value;return replace(reply,values=tuple(values))
                    return replace(reply,**{field:value})
                self.mutate=mutate;self.denied(reason)
    def test_mismatched_reference_cannot_allow_an_action(self):
        self.mutate=lambda reply:replace(reply,values=reply.values[:2]+(999,)+reply.values[3:])
        self.denied('reference_mismatch')
    def test_feedback_must_be_known_and_still_fresh_at_dispatch(self):
        self.denied('unknown_feedback_age',replace(self.sample,feedback_age_us=UNKNOWN))
        self.setUp();self.delay_us=2000
        self.denied('feedback_expired_before_dispatch',replace(self.sample,feedback_age_us=99_000))
    def test_stop_while_waiting_never_sends_the_candidate(self):
        self.denied('stop_requested',stop_requested=lambda:bool(self.requests))
    def test_already_expired_cycle_does_not_even_request_a_permit(self):
        with self.assertRaisesRegex(AuthorityDenied,'deadline_before_send'):
            self.gate.send(self.sample,tuple(v/1e6 for v in self.sample.target),self.sent.append,deadline_ns=self.now)
        self.assertEqual(self.requests,[]);self.assertEqual(self.sent,[])
    def test_candidate_and_checked_payload_must_be_identical(self):
        with self.assertRaisesRegex(AuthorityDenied,'candidate_payload_mismatch'):
            self.gate.send(self.sample,(0.,)*14,self.sent.append,deadline_ns=self.now+30_000_000)
        self.assertEqual(self.requests,[]);self.assertEqual(self.sent,[])
    def test_dispatch_failure_never_retries_the_action(self):
        def broken(_):raise OSError('uncertain actuator acknowledgement')
        with self.assertRaises(OSError):
            self.gate.send(self.sample,tuple(v/1e6 for v in self.sample.target),broken,deadline_ns=self.now+30_000_000)
        self.assertEqual(self.gate.records[-1]['decision'],'DISPATCH_ERROR')
        self.denied('latched:dispatch_error');self.assertEqual(len(self.requests),1)
    def test_real_client_and_board_session_protocol_control_the_actuator_callback(self):
        profile=self.profile;crc=profile_crc(profile)
        class Guard:
            guard_crc=crc
            def check(self,sample,session,sequence,reset_session=False):
                ref=evaluate(sample,profile)
                return wire.FPG1_REPLY.pack(crc,ref['violations'],ref['events'],ref['phase'],2000,0,4,15,wire.BUILD_ID,UNKNOWN)
        policy=FoldSession('127.0.0.1',Guard(),profile)
        class MemorySocket:
            def __init__(self,*args):self.queue=[]
            def connect(self,address):pass
            def settimeout(self,value):pass
            def close(self):pass
            def send(self,packet):
                reply=policy.handle(packet,('127.0.0.1',30303))
                # Exercise the actual client's CRC/identity filtering first.
                self.queue.extend([reply[:-1]+bytes([reply[-1]^1]),reply])
            def recv(self,size):return self.queue.pop(0)
        client=FoldClient(('127.0.0.1',9009),crc,socket_factory=MemorySocket)
        client.probe(replace(self.sample,step=UNKNOWN,flags=0,elapsed_us=0))
        gate=CheckedSender(client,profile,reply_budget_us=1_000_000,feedback_limit_us=100_000)
        outputs=[]
        gate.send(self.sample,tuple(v/1e6 for v in self.sample.target),outputs.append,deadline_ns=__import__('time').perf_counter_ns()+1_000_000_000)
        target=list(self.sample.target);target[8]=2650000
        bad=replace(self.sample,step=1,elapsed_us=33334,target=tuple(target))
        with self.assertRaisesRegex(AuthorityDenied,'pl_violation'):
            gate.send(bad,tuple(v/1e6 for v in bad.target),outputs.append,deadline_ns=__import__('time').perf_counter_ns()+1_000_000_000)
        self.assertEqual(len(outputs),1);self.assertEqual(policy.last_step,1)
        self.assertTrue(gate.records[0]['dispatched']);self.assertFalse(gate.records[1]['dispatched'])
        client.close()


if __name__=='__main__':unittest.main()
