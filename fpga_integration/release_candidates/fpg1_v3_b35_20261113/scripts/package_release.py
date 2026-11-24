"""Package board runtime, standard-library PC tools, source and verification records."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

WS=Path(__file__).resolve().parents[1]


def copy_file(source,destination):
    destination.parent.mkdir(parents=True,exist_ok=True)
    if source.suffix=='.sh':
        destination.write_bytes(source.read_bytes().replace(b'\r\n',b'\n'))
    else:shutil.copy2(source,destination)


def copy_tree(source,destination):
    for path in sorted(source.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts:
            if path.is_symlink():raise ValueError('source symlink: '+str(path))
            copy_file(path,destination/path.relative_to(source))


def seal(directory):
    files=sorted(p for p in directory.rglob('*') if p.is_file() and p!=directory/'SHA256SUMS')
    (directory/'SHA256SUMS').write_bytes(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(directory).as_posix()+'\n' for p in files).encode('ascii'))


def archive(directory,target):
    with zipfile.ZipFile(target,'x',zipfile.ZIP_DEFLATED) as output:
        for path in sorted(directory.rglob('*')):
            if path.is_file():output.write(path,directory.name+'/'+path.relative_to(directory).as_posix())
    with zipfile.ZipFile(target) as bundle:
        if bundle.testzip() is not None:raise ValueError('archive CRC failed')
        for row in (directory/'SHA256SUMS').read_text().splitlines():
            expected,relative=row.split('  ',1)
            if hashlib.sha256(bundle.read(directory.name+'/'+relative)).hexdigest()!=expected:
                raise ValueError('archive checksum failed: '+relative)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True,help='new release directory')
    args=parser.parse_args()
    evidence=WS.parent/'validation_runs'
    expected=json.loads((evidence/'20261101_offline_complete/compatibility.json').read_text())['hardware_files_unchanged']
    for relative,digest in expected.items():
        if hashlib.sha256((WS/relative).read_bytes()).hexdigest()!=digest:
            raise ValueError('firmware differs from the validated baseline: '+relative)
    args.out.mkdir(parents=True,exist_ok=False)
    board=args.out/'board_runtime';pc=args.out/'aipc_client';full=args.out/'fpga_code'
    board.mkdir();pc.mkdir();full.mkdir()
    for folder in ('board','screen','config'):
        copy_tree(WS/folder,board/folder)
    client_files=('__init__.py','protocol.py','reference.py','link.py','watchdog_protocol.py',
                  'watchdog_demo.py','watchdog_report.py','task_status.py','coop_demo.py','runtime_report.py')
    for name in client_files:
        copy_file(WS/'host'/name,board/'host'/name)
        copy_file(WS/'host'/name,pc/'host'/name)
    copy_file(WS/'rtl/vectors.txt',board/'board/vectors.txt')
    copy_file(WS/'scripts/run_fpga.sh',board/'scripts/run_fpga.sh')
    copy_file(WS/'docs/RUNBOOK.md',board/'README.md')
    copy_file(WS/'docs/OFFLINE_COMPLETION.md',board/'docs/OFFLINE_COMPLETION.md')
    for folder in ('config','examples'):
        copy_tree(WS/folder,pc/folder)
    copy_file(WS/'docs/RUNBOOK.md',pc/'README.md')
    copy_file(WS/'docs/OFFLINE_COMPLETION.md',pc/'docs/OFFLINE_COMPLETION.md')
    for folder in ('board','host','screen','config','examples','rtl','tests','docs','scripts','source_snapshot','replay_board_20261031'):
        copy_tree(WS/folder,full/'fpga_workspace'/folder)
    for name in ('README.md','VERIFY_PACKAGE.py','build_pynq_overlay.tcl','build_pynq_overlay_watchdog.tcl','build_pynq_lcd_overlay.tcl'):
        copy_file(WS/name,full/'fpga_workspace'/name)
    copy_file(WS/'rtl/vectors.txt',full/'fpga_workspace/board/vectors.txt')
    for relative in ('own_hybrid_motion_guard_watchdog.bit','own_hybrid_motion_guard_watchdog.hwh',
                     'lcd_framebuffer_20261101/lcd1_guard.bit','lcd_framebuffer_20261101/lcd1_guard.hwh'):
        for destination in (board,full/'fpga_workspace'):
            copy_file(WS/'overlay'/relative,destination/'overlay'/relative)
    for path in (WS/'overlay/lcd_framebuffer_20261101').glob('*.txt'):
        copy_file(path,full/'fpga_workspace/overlay/lcd_framebuffer_20261101'/path.name)
    copy_tree(evidence/'20261101_offline_complete',full/'validation_runs/20261101_offline_complete')
    previous=evidence/'20261101_lcd_python'
    for name in ('LCD1_BOARD_VALIDATION.md','lcd1_physical_validation_manifest.json','lcd1_board_evidence_001.tar.gz'):
        copy_file(previous/name,full/'validation_runs/20261101_lcd_python'/name)
    copy_tree(previous/'lcd1_board_evidence_001',full/'validation_runs/20261101_lcd_python/lcd1_board_evidence_001')
    seal(full/'fpga_workspace')
    checked=subprocess.run([sys.executable,'-B','VERIFY_PACKAGE.py'],cwd=full/'fpga_workspace',
                           capture_output=True,text=True,timeout=100)
    (args.out/'verification_console.txt').write_bytes((checked.stdout+checked.stderr).encode('utf-8'))
    if checked.returncode:raise RuntimeError(checked.stdout+checked.stderr)
    verification=json.loads(checked.stdout)
    for destination in (args.out/'package_verification.json',full/'validation_runs/20261101_offline_complete/package_verification.json'):
        destination.write_bytes((json.dumps(verification,indent=2)+'\n').encode('utf-8'))
    manifest=dict(software_implementation_complete=True,new_runtime_physically_verified=False,
        previous_firmware_physically_verified=True,production_request_interval='not measured',
        target_aipc_os='Linux; exact environment not yet tested',robot_commands=False,
        verification=verification,archives=[])
    for directory in (board,pc,full):
        seal(directory)
        target=args.out/(directory.name+'.zip')
        archive(directory,target)
        manifest['archives'].append(dict(name=target.name,bytes=target.stat().st_size,
            sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
    (args.out/'RELEASE.json').write_bytes((json.dumps(manifest,indent=2)+'\n').encode('utf-8'))
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':main()
