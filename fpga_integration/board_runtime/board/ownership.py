"""Cooperative process lock held through PL programming and final restoration."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile


@contextmanager
def exclusive_owner():
    path=Path(tempfile.gettempdir())/'fpga_monitor_pl_owner.lock'
    stream=path.open('a+b')
    try:
        if os.name=='nt':
            import msvcrt
            if stream.seek(0,2)==0:
                stream.write(b'0');stream.flush()
            stream.seek(0)
            try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as error:raise RuntimeError('another FPGA service owns the PL') from error
        else:
            import fcntl
            try:fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError as error:raise RuntimeError('another FPGA service owns the PL') from error
        yield
    finally:
        stream.close()
