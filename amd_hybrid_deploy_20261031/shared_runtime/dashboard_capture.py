"""Capture only this Tk window on Linux/Xwayland for UI verification."""
import ctypes as C
from PIL import Image
class XImage(C.Structure):
    _fields_=[('width',C.c_int),('height',C.c_int),('xoffset',C.c_int),('format',C.c_int),('data',C.c_void_p),('byte_order',C.c_int),('bitmap_unit',C.c_int),('bitmap_bit_order',C.c_int),('bitmap_pad',C.c_int),('depth',C.c_int),('bytes_per_line',C.c_int),('bits_per_pixel',C.c_int)]
def capture(widget):
    x=C.CDLL('libX11.so.6')
    x.XOpenDisplay.argtypes=[C.c_char_p];x.XOpenDisplay.restype=C.c_void_p
    x.XGetImage.argtypes=[C.c_void_p,C.c_ulong,C.c_int,C.c_int,C.c_uint,C.c_uint,C.c_ulong,C.c_int];x.XGetImage.restype=C.POINTER(XImage)
    x.XDestroyImage.argtypes=[C.POINTER(XImage)];x.XCloseDisplay.argtypes=[C.c_void_p]
    display=x.XOpenDisplay(None)
    if not display:raise RuntimeError('No X display')
    value=x.XGetImage(display,widget.winfo_id(),0,0,widget.winfo_width(),widget.winfo_height(),C.c_ulong(-1).value,2)
    if not value:
        x.XCloseDisplay(display)
        raise RuntimeError('Cannot capture window')
    try:
        info=value.contents
        raw=C.string_at(info.data,info.bytes_per_line*info.height)
        assert info.bits_per_pixel in (24,32), info.bits_per_pixel
        return Image.frombytes('RGB',(info.width,info.height),raw,'raw','BGRX' if info.bits_per_pixel==32 else 'BGR',info.bytes_per_line,1)
    finally:x.XDestroyImage(value);x.XCloseDisplay(display)
