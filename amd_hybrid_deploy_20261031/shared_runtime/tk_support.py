"""Use system Tk, or the optional locally unpacked Ubuntu Tk runtime."""
import os
from pathlib import Path
import sys


def load_tk():
    try:
        import tkinter
        return tkinter
    except ModuleNotFoundError as error:
        if error.name not in ('tkinter', '_tkinter'):
            raise
    root = Path(__file__).resolve().parents[1]/'.ui-runtime/usr'
    python = root/'lib'/f'python{sys.version_info.major}.{sys.version_info.minor}'
    if not (python/'tkinter').is_dir():
        raise RuntimeError('缺少 Tk：安装 python3-tk，或运行 tools/prepare_dashboard_ui.sh。')
    import ctypes
    libraries = root/'lib/x86_64-linux-gnu'
    for name in ('libXss.so.1', 'libtk8.6.so.0'):
        path = libraries/name
        if path.exists():
            ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL)
    blt=root/'lib/libBLT.2.5.so.8.6'
    if blt.exists():ctypes.CDLL(str(blt), mode=ctypes.RTLD_GLOBAL)
    sys.path.extend((str(python), str(python/'lib-dynload')))
    os.environ.setdefault('TK_LIBRARY', str(root/'share/tcltk/tk8.6'))
    import tkinter
    return tkinter
