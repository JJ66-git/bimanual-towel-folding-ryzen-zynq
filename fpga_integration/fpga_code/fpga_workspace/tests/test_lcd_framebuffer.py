import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image
from screen.lcd import PALETTE, encode_portrait, decode_native, Framebuffer, FRAME_BASE, PAGE_STRIDE, PAGE_BYTES
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]


class FramebufferTests(unittest.TestCase):
    def test_initial_pages_are_complete_before_enable_and_close_can_preserve_display(self):
        class FakeIP:
            def __init__(self):
                self.mmio=SimpleNamespace(array=np.zeros(0x80000//4,dtype=np.uint32))
                self.regs={0:0x4c434431,0x1c:480<<16|800,0x28:PAGE_BYTES,8:16,0x0c:0x4384}
                self.enabled_frames=[]
            def read(self,offset):return self.regs.get(offset,0)
            def write(self,offset,value):
                self.regs[offset]=value
                if offset==4:self.regs[8]=(self.regs[8]&~8)|(value<<3)
                if offset==0x10:
                    self.regs[0x14]=self.regs.get(0x14,0)+1
                    self.regs[8]=(self.regs[8]&~6)|1|(value<<1)
                if offset==4 and value:
                    for page in (0,1):
                        start=(FRAME_BASE+page*PAGE_STRIDE)//4
                        self.enabled_frames.append(self.mmio.array[start:start+PAGE_BYTES//4].tobytes())
        ip=FakeIP();initial=Image.new('RGB',(480,800),PALETTE[13])
        display=Framebuffer(ip,initial_image=initial)
        self.assertEqual(ip.enabled_frames,[encode_portrait(initial)]*2)
        display.close(preserve=True);self.assertEqual(ip.regs[4],1)
        display.close();self.assertEqual(ip.regs[4],0)

    def test_exact_native_pixels_orientation_and_nibble_order(self):
        native=np.empty((480,800,3),dtype=np.uint8)
        expected=np.empty((480,800),dtype=np.uint8)
        for y in range(480):
            expected[y]=(np.arange(800)//100+y//60)%16
        native[:]=np.asarray(PALETTE,dtype=np.uint8)[expected]
        portrait=Image.fromarray(native).transpose(Image.ROTATE_270)
        packed=encode_portrait(portrait)
        self.assertEqual(len(packed),192000)
        self.assertEqual(packed[0],0)
        self.assertEqual(packed[50],0x11)
        np.testing.assert_array_equal(np.asarray(decode_native(packed)),native)
        with self.assertRaises(ValueError): encode_portrait(Image.new('RGB',(800,480)))

    def test_prepare_cli_produces_the_exact_hardware_format_and_preview(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary); source=directory/'image.png'; out=directory/'prepared'
            Image.new('RGB',(480,800),(255,0,0)).save(source)
            result=subprocess.run([sys.executable,'-B','-m','screen.prepare','--image',str(source),
                                   '--out',str(out)],cwd=ROOT,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual((out/'frame.bin').read_bytes(),bytes([0xdd])*192000)
            with Image.open(out/'portrait_preview.png') as preview:
                self.assertEqual(preview.size,(480,800))
                self.assertEqual(preview.getpixel((479,799)),(255,0,0))
            self.assertFalse(json.loads((out/'manifest.json').read_text())['physical_display_verified'])


if __name__=='__main__':
    unittest.main()
