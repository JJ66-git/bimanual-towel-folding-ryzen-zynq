import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image
from screen.lcd import PALETTE, encode_portrait, decode_native

ROOT=Path(__file__).resolve().parents[1]


class FramebufferTests(unittest.TestCase):
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
