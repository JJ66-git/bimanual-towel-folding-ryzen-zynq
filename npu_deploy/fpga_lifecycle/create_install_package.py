#!/usr/bin/env python3
"""Build a private Jupyter-upload package with a per-install authentication key."""
import io
import os
from pathlib import Path
import secrets
import tarfile

HERE=Path(__file__).resolve().parent
OUT=HERE.parent/'artifacts/fpga_lifecycle'
OUT.mkdir(parents=True,exist_ok=True,mode=0o700)
os.chmod(OUT,0o700)
key=OUT/'control.key'
if not key.exists():
    fd=os.open(str(key),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream: stream.write(secrets.token_hex(32)+'\n')
if len(bytes.fromhex(key.read_text().strip()))!=32:
    raise SystemExit('Existing FPGA control key invalid; installation package was not created.')
os.chmod(key,0o600)
package=OUT/'fpga_lifecycle_install.tar.gz'
temporary=OUT/'.fpga_lifecycle_install.tmp'
fd=os.open(str(temporary),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'wb') as stream:
    with tarfile.open(fileobj=stream,mode='w:gz') as archive:
        for name,path in [('board_manager.py',HERE/'board_manager.py'),('control_protocol.py',HERE/'control_protocol.py'),
                          ('install_on_board.sh',HERE/'install_on_board.sh'),('control.key',key)]:
            data=path.read_bytes(); info=tarfile.TarInfo('fpga_lifecycle_install/'+name)
            info.size=len(data); info.mode=0o600 if name=='control.key' else 0o700 if name.endswith('.sh') else 0o644
            info.mtime=0
            archive.addfile(info,io.BytesIO(data))
os.replace(temporary,package)
os.chmod(package,0o600)
print('LIFECYCLE_PACKAGE='+str(package))
print('安装包含私人控制密钥；请只上传到自己的 FPGA，勿公开或加入 Git。')
