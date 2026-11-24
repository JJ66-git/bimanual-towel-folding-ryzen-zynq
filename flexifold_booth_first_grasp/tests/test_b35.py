"""Current B35 session plus the booth client, with in-memory datagrams only."""
import importlib.util
from pathlib import Path
import threading
import sys
import unittest
from unittest.mock import patch
import host
from host import fold_protocol as wire
from booth import Booth,ROOT
from verify_fpga import verify,verify_ready
from test_booth import MemoryDatagram,ReferenceGuard

WORKSPACE=ROOT.parent/'fpga_integration/fpga_code/fpga_workspace'
if WORKSPACE.is_dir():
    host.__path__.append(str(WORKSPACE/'host'));sys.path.append(str(WORKSPACE))


@unittest.skipUnless(WORKSPACE.is_dir(),'current B35 runtime is not included in standalone booth copies')
class B35Tests(unittest.TestCase):
    def test_startup_connection_refusal_is_retried_without_synthesizing_a_pass(self):
        with patch('verify_fpga.verify',side_effect=[ConnectionRefusedError('starting'),[{'source':'board'}]]) as check,patch('verify_fpga.time.sleep'):
            self.assertEqual(verify_ready(object()),[{'source':'board'}]);self.assertEqual(check.call_count,2)
        with patch('verify_fpga.verify',side_effect=ConnectionRefusedError('stopped')):
            with self.assertRaises(ConnectionRefusedError):verify_ready(object(),timeout=0)

    def test_booth_render_uses_compatible_primitives_and_the_shared_palette(self):
        from PIL import ImageDraw
        from screen.cooperative_view import PALETTE
        spec=importlib.util.spec_from_file_location('booth_renderer_test',ROOT/'board_addon/booth_display.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with patch.object(ImageDraw.ImageDraw,'rounded_rectangle',side_effect=AssertionError('unsupported Pillow API')):
            for mask in (None,0,1):
                image=module.render(dict(mask=mask,value=2.65,sequence=4,session=12))
                self.assertEqual(image.size,(480,800));self.assertEqual(image.getpixel((0,0)),PALETTE[0])

    def setUp(self):
        spec=importlib.util.spec_from_file_location('current_b35_session',WORKSPACE/'board/fold_bridge.py')
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        self.transport=MemoryDatagram()
        with patch('host.board_link.socket.socket',return_value=self.transport):self.booth=Booth('127.0.0.1',9009)
        self.guard=ReferenceGuard(self.booth)
        self.transport.policy=bridge.FoldSession('127.0.0.1',self.guard,self.booth.profile)
    def tearDown(self):
        self.booth.collector.close();self.booth.board_link.close()
    def test_current_session_accepts_the_interactive_boundary_sequence(self):
        rows=verify(self.booth)
        self.assertEqual([r['mask'] for r in rows],[1,0,1,0])
        self.assertTrue(all(r['build_id']==0x46504735 for r in rows))
        self.assertEqual([r['seq'] for r in rows],[1,2,3,4])
        self.assertTrue(all(r['reference_match'] for r in rows))
        self.assertEqual(self.transport.errors,[])
    def test_b34_reply_is_rejected_without_a_software_pass(self):
        original=self.guard.check
        def old_reply(*args,**kwargs):
            values=list(wire.FPG1_REPLY.unpack(original(*args,**kwargs)));values[8]=0x46504734
            return wire.FPG1_REPLY.pack(*values)
        self.guard.check=old_reply
        with self.assertRaises(TimeoutError):self.booth.result(self.booth.sample_for_angle(2540))
    def test_only_task_replies_replace_the_lcd_diagnostic(self):
        spec=importlib.util.spec_from_file_location('booth_display_test',ROOT/'board_addon/booth_display.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        display=module.BoothDisplay.__new__(module.BoothDisplay)
        display.lock=threading.Lock();display.state={}
        values=(1,wire.UNKNOWN,0,0,wire.UNKNOWN,wire.UNKNOWN)+(0,)*84
        request=wire.Frame(b'FPG1',3,1,1,0,1,values)
        reply=wire.Frame(b'FPG1',3,2,1,0,1,(1,0,0,0,1,0,0,1,wire.BUILD_ID,wire.UNKNOWN))
        display.accepted(request,reply,1,1);self.assertEqual(display.state,{})
        values=list(values);values[1]=201;values[6+8]=2650000
        request=wire.Frame(b'FPG1',3,1,1,1,2,tuple(values))
        reply=wire.Frame(b'FPG1',3,2,1,1,2,(1,1,0,0,1,0,0,1,wire.BUILD_ID,wire.UNKNOWN))
        display.accepted(request,reply,2,1)
        self.assertEqual(display.state['mask'],1)
        self.assertEqual(display.state['value'],2.65)
        self.assertEqual(display.state['session'],1)


if __name__=='__main__':unittest.main()
