"""Display receipts cannot turn an old PL pass into permission or task success."""
import json
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
import zlib

from host import action_status as wire
from host.action_status_publisher import Publisher
from screen.cooperative_state import CooperativeState
from host import fold_protocol as fpg
from host.protocol import Sample,RUNNING,UNKNOWN
from host.reference import load_profile,profile_crc

class ReceiptTests(unittest.TestCase):
    def setUp(self):self.receiver=wire.Receiver();self.owner=(('127.0.0.1',100),42);self.address=('127.0.0.1',101)
    def accept(self,row,seq=0,session=42,last=1,mask=0,address=None,source='synthetic_test'):
        return self.receiver.accept(wire.pack(1,session,seq,seq+100,row),address or self.address,self.owner,last,source,mask)
    def test_every_stage_roundtrips_without_claiming_execution(self):
        for stage in wire.STAGES:
            row=wire.values(stage,outcome='sent' if stage=='dispatched' else 'unknown' if stage in ('dispatch_unknown','awaiting_receipt') else 'not_sent')
            self.assertEqual(wire.unpack(wire.pack(1,42,3,9,row)),(1,42,3,9,row))
            self.assertNotIn('执行完成',wire.presentation(row)['headline'])
    def test_ambiguous_and_corrupt_values_are_rejected(self):
        good=wire.pack(1,42,0,1,wire.values('waiting'));bad=bytearray(good);bad[-1]^=1
        for packet in (b'',good[:-1],good+b'x',bad):
            with self.assertRaises(ValueError):wire.unpack(packet)
        for key,value in (('mask',1<<14),('step',True),('target_sequence',-1),('budget_us',0),('sink','robot_executed'),('reason','x'*97),('outcome','sent')):
            row=dict(wire.values('waiting'));row[key]=value
            with self.assertRaises(ValueError):wire.validate(row)
        payload=json.dumps(wire.values('waiting')).replace('"stage": "waiting"','"stage": "waiting", "stage": "ended"').encode()
        body=wire.HEADER.pack(b'FAS1',1,1,len(payload),42,0,1)+payload
        with self.assertRaisesRegex(ValueError,'duplicate'):wire.unpack(body+wire.CRC.pack(zlib.crc32(body)&UNKNOWN))
    def test_wrong_session_sender_replay_and_future_target_rejected(self):
        row=wire.values('checking',sink='memory',target_sequence=2)
        with self.assertRaisesRegex(ValueError,'session'):self.accept(row,session=43)
        self.accept(row)
        with self.assertRaisesRegex(ValueError,'old_receipt'):self.accept(row)
        with self.assertRaisesRegex(ValueError,'sender'):self.accept(row,seq=1,address=('127.0.0.1',102))
        with self.assertRaisesRegex(ValueError,'old_target'):self.accept(dict(row,target_sequence=3),seq=1)
    def test_pass_requires_current_checked_zero_mask(self):
        row=wire.values('allowed',sink='memory',target_sequence=1,mask=0)
        for change,mask in (({},1),({'target_sequence':2},0),({'mask':1},0)):
            with self.assertRaises(ValueError):self.accept(dict(row,**change),mask=mask)
        self.accept(row)
    def test_synthetic_source_cannot_claim_real_robot_send(self):
        with self.assertRaisesRegex(ValueError,'synthetic_robot'):self.accept(wire.values('dispatched',target_sequence=1,mask=0,outcome='sent'))
    def test_denial_latches_until_new_fpg_session(self):
        self.accept(wire.values('denied',sink='memory',target_sequence=1,reason='no_matching_reply'))
        for stage in ('checking','allowed','dispatched'):
            row=wire.values(stage,sink='memory',target_sequence=1,mask=0,outcome='sent' if stage=='dispatched' else 'not_sent')
            with self.assertRaisesRegex(ValueError,'latched'):self.accept(row,seq=1)
        self.accept(wire.values('ended',sink='memory',target_sequence=1),seq=1)
        self.owner=(self.owner[0],43)
        self.accept(wire.values('waiting',sink='memory'),session=43,last=0)
    def test_stale_pass_is_history_but_denial_stays_visible(self):
        self.assertEqual(wire.presentation(wire.values('allowed'),False)['headline'],'核准数据已停更')
        self.assertEqual(wire.presentation(wire.values('denied'),False)['headline'],'本条已拦截')
    def test_background_publisher_receives_correlated_ack_and_closes(self):
        server=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);server.bind(('127.0.0.1',0));server.settimeout(2)
        errors=[]
        def respond():
            try:
                data,address=server.recvfrom(4096);kind,session,seq,tick,row=wire.unpack(data)
                server.sendto(wire.pack(129,session,seq,tick,row),address)
            except Exception as error:errors.append(str(error))
        worker=threading.Thread(target=respond);worker.start();publisher=Publisher(server.getsockname(),42,interval=.001)
        row=wire.values('checking',sink='memory',target_sequence=1)
        try:
            publisher.publish(row);deadline=time.monotonic()+2
            while publisher.delivered!=row and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(publisher.delivered,row);self.assertIsNone(publisher.error)
        finally:publisher.close();worker.join(2);server.close()
        self.assertFalse(publisher.worker.is_alive());self.assertEqual(errors,[])

class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.clock=[1000000000];self.model=CooperativeState('synthetic_test',clock_ns=lambda:self.clock[0]);self.profile=load_profile()
        self.packet(1,0,session=42)
    def packet(self,sequence,mask,session=42):
        pose=tuple(self.profile['start']);zero=(0,)*14
        sample=Sample(profile_crc(self.profile),sequence-1,RUNNING,1000,1000,0,pose,pose,pose,pose,zero,zero)
        request=fpg.Frame(b'FPG1',3,1,session,sequence,sequence,fpg.FPG1_REQUEST.unpack(sample.pack()))
        reply=fpg.Frame(b'FPG1',3,2,session,sequence,sequence,(profile_crc(self.profile),mask,0,3,1000,0,4,15,fpg.BUILD_ID,UNKNOWN))
        self.model.accepted(request,reply,self.clock[0],1000)
    def claim(self,stage,**kwargs):self.model.authority_claim(wire.values(stage,sink='memory',target_sequence=1,**kwargs),self.clock[0],42)
    def test_new_sample_invalidates_previous_send_receipt(self):
        self.claim('dispatched',mask=0,outcome='sent');self.packet(2,0)
        state=self.model.snapshot();self.assertEqual(state['authority']['stage'],'awaiting_receipt');self.assertEqual(state['authority']['outcome'],'unknown')
    def test_old_pl_pass_cannot_overwrite_current_timeout(self):
        self.claim('denied',reason='no_matching_reply');self.packet(2,0)
        state=self.model.snapshot();self.assertEqual(state['headline'],'本条已拦截');self.assertEqual(state['task_result'],'unconfirmed')
        self.clock[0]+=10**10;self.assertEqual(self.model.snapshot()['headline'],'本条已拦截')
    def test_memory_receipt_never_certifies_folding_success(self):
        self.claim('ended');self.model.host_claim(dict(phase='COMPLETE',activity='IDLE',origin='OPERATOR'),self.clock[0],42)
        self.assertEqual(self.model.snapshot()['task_result'],'unconfirmed')
    def test_new_session_removes_old_denial(self):
        self.claim('denied',reason='pl_violation');self.packet(1,0,session=43)
        self.assertIsNone(self.model.snapshot()['authority']);self.assertNotEqual(self.model.snapshot()['headline'],'本条已拦截')
    def test_both_lcd_fonts_cover_every_authority_character(self):
        from fontTools.ttLib import TTFont
        workspace=Path(__file__).resolve().parents[1]
        chars={ord(char) for char in (workspace/'host/action_status.py').read_text() if '\u4e00'<=char<='\u9fff'}
        for name in ('DisplaySansSC.otf','DisplaySansSC-Bold.otf'):
            with TTFont(workspace/'screen/assets'/name) as font:
                self.assertFalse(chars-set(font.getBestCmap()),name)
    def test_lcd_and_desktop_share_state_meaning_and_reject_old_pass(self):
        root=Path(__file__).resolve().parents[4];sys.path.insert(0,str(root/'amd_hybrid_deploy_20261031'))
        from shared_runtime.dashboard_view import DashboardState,authority_projection,render_dashboard,diagnostic_payload
        from shared_runtime.dashboard_telemetry import AuthoritySnapshot,ControlSnapshot,BoardSnapshot,BoardLink
        now=time.monotonic();row=wire.values('denied',sink='memory',target_sequence=2,step=1,reason='no_matching_reply')
        state=DashboardState('REPLAY');control=ControlSnapshot('test',now,now,step=0,run_state='running')
        state.ingest(dict(control=control,board_link=BoardLink(('192.168.2.99',9009),42,'crc',750,750),authority=AuthoritySnapshot(now,42,row),board=BoardSnapshot(now,42,1,0,True,0)))
        self.assertEqual(authority_projection(state,now),wire.presentation(row));self.assertEqual(diagnostic_payload(state)['authority']['values'],row)
        for width,height in ((860,620),(1200,800),(1520,940)):
            for page in ('cameras','fpga'):self.assertEqual(render_dashboard(state,width,height,now=now,page=page)[0].size,(width,height))
        state.ingest(dict(control=ControlSnapshot('new',now+1,now+1)));self.assertIsNone(state.authority)

if __name__=='__main__':unittest.main()
