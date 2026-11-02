"""16-color portrait frames for the integrated LCD1 AXI framebuffer."""
import time

import numpy as np
from PIL import Image

PALETTE = ((5,17,29),(9,30,47),(28,211,235),(236,245,255),
           (118,142,163),(37,230,167),(255,105,85),(255,179,71),
           (70,140,255),(9,37,43),(54,82,105),(40,53,65),
           (0,0,0),(255,0,0),(0,255,0),(0,0,255))
PAGE_BYTES = 192000
PAGE_STRIDE = 0x30000
FRAME_BASE = 0x1000


def encode_portrait(image):
    if image.size != (480,800):
        raise ValueError('portrait image must be 480x800')
    native = image.convert('RGB').transpose(Image.ROTATE_90)
    colors = Image.new('P',(1,1))
    colors.putpalette([v for color in PALETTE for v in color] * 16)
    indexed = native.quantize(palette=colors,dither=Image.NONE)
    indices = np.asarray(indexed,dtype=np.uint8).reshape(-1) & 15
    packed = indices[0::2] | (indices[1::2] << 4)
    return packed.tobytes()


def decode_native(packed):
    if len(packed) != PAGE_BYTES:
        raise ValueError('incorrect frame byte count')
    data = np.frombuffer(packed,dtype=np.uint8)
    indices = np.empty(data.size*2,dtype=np.uint8)
    indices[0::2] = data & 15
    indices[1::2] = data >> 4
    rgb = np.asarray(PALETTE,dtype=np.uint8)[indices].reshape(480,800,3)
    return Image.fromarray(rgb,'RGB')


class Framebuffer:
    def __init__(self, ip):
        self.ip = ip
        if ip.read(0) != 0x4c434431:
            raise RuntimeError('LCD1 BUILD_ID mismatch')
        if ip.read(0x1c) != (480 << 16 | 800) or ip.read(0x28) != PAGE_BYTES:
            raise RuntimeError('LCD geometry mismatch')
        deadline = time.perf_counter()+1
        while not ip.read(8) & 16:
            if time.perf_counter() >= deadline:
                raise TimeoutError('LCD ID sampling timeout')
            time.sleep(0.001)
        panel = ip.read(0x0c)
        if panel != 0x4384:
            raise RuntimeError('unverified LCD ID: 0x{:04x}'.format(panel))
        status = ip.read(8)
        if status & 4:
            raise RuntimeError('LCD has an outstanding frame; keep one display owner')
        for index,color in enumerate(PALETTE):
            rgb = color[0] << 16 | color[1] << 8 | color[2]
            offset = 0x100+4*index
            if not status & 1:
                ip.write(offset,rgb)
            if ip.read(offset) != rgb:
                raise RuntimeError('LCD palette mismatch')
        ip.write(0x04,1)

    def close(self):
        self.ip.write(0x04,0)

    def present(self, image):
        packed = encode_portrait(image)
        status = self.ip.read(8)
        if status & 4 or not status & 8:
            raise RuntimeError('LCD busy or disabled')
        back = 1 - ((status >> 1) & 1)
        start = (FRAME_BASE+back*PAGE_STRIDE)//4
        words = np.frombuffer(packed,dtype='<u4')
        self.ip.mmio.array[start:start+len(words)] = words
        previous = self.ip.read(0x14)
        self.ip.write(0x10,back)
        deadline = time.perf_counter()+0.25
        while self.ip.read(0x14) == previous:
            if time.perf_counter() >= deadline:
                raise TimeoutError('LCD vertical-blank page flip timeout')
            time.sleep(0.001)
        status = self.ip.read(8)
        if status & 4 or not status & 1 or (status >> 1) & 1 != back:
            raise RuntimeError('LCD page flip acknowledgement mismatch')
        return dict(generation=int(self.ip.read(0x14)),front_page=back,
                    scan_frames=int(self.ip.read(0x18)),panel_id=int(self.ip.read(0x0c)),
                    physical_display_verified=False)
