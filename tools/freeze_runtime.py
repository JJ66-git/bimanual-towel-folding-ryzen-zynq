#!/usr/bin/env python3
"""Freeze model/runtime files outside Git before experimental development.

Copies file contents (never hardlinks), preserves symlinks and records every
non-cache file's SHA-256. It does not import drivers, connect to a board or run CAN.
System Python, kernel drivers and external /opt symlink targets remain unchanged.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]
SKIP={'__pycache__','.cache'}


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda:stream.read(4*1024*1024),b''):h.update(data)
    return h.hexdigest()


def inventory(root):
    rows=[]
    def one(path,relative):
        if path.is_symlink():rows.append(dict(path=str(relative),kind='symlink',target=os.readlink(path)))
        else:rows.append(dict(path=str(relative),kind='file',bytes=path.stat().st_size,sha256=digest(path),mode=path.stat().st_mode&0o777))
    if root.is_file():one(root,Path('.'));return rows
    for current,dirs,files in os.walk(root):
        dirs[:]=sorted(name for name in dirs if name not in SKIP)
        for name in list(dirs):
            path=Path(current)/name
            if path.is_symlink():one(path,path.relative_to(root));dirs.remove(name)
        for name in sorted(files):
            if name.endswith(('.pyc','.pyo')):continue
            path=Path(current)/name;one(path,path.relative_to(root))
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-workspace',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--plan',action='store_true')
    args=parser.parse_args();source=args.source_workspace.resolve(strict=True);out=args.out.resolve()
    dependencies=json.loads((ROOT/'baseline/dependencies.json').read_text())['dependencies']
    if args.plan:
        print(json.dumps(dict(destination=str(out),roles=[d['role'] for d in dependencies],free_bytes=shutil.disk_usage(out.parent if out.parent.exists() else source).free),ensure_ascii=False));return 0
    out.mkdir(parents=True,exist_ok=True)
    lock=out/'.freeze.lock'
    import fcntl
    with lock.open('a+b') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        reports=[]
        for dependency in dependencies:
            role=dependency['role'];original=(source/dependency['source']).resolve(strict=True);target=out/role
            record_path=out/(role+'.manifest.json')
            if record_path.exists():
                record=json.loads(record_path.read_text())
                if inventory(target)!=record['files']:raise RuntimeError('Frozen backup changed: '+role)
                reports.append(record);print('RUNTIME_FREEZE_REUSED='+role,flush=True);continue
            for item in dependency['files']:
                path=original if item['path']=='.' else original/item['path']
                if path.stat().st_size!=item['bytes'] or digest(path)!=item['sha256']:
                    raise RuntimeError('Baseline asset identity changed before backup: '+role+'/'+item['path'])
            print('RUNTIME_FREEZE_COPY='+role,flush=True);started=time.monotonic()
            if original.is_dir():
                target.mkdir(exist_ok=True)
                subprocess.run(['rsync','-a','--no-owner','--no-group','--exclude=__pycache__','--exclude=*.pyc','--exclude=*.pyo','--exclude=.cache',str(original)+'/',str(target)+'/'],check=True)
            else:shutil.copy2(original,target)
            saved=inventory(target);live=inventory(original)
            if saved!=live:raise RuntimeError('Source changed while copying: '+role)
            record=dict(role=role,source=str(original),backup=str(target),files=saved,seconds=time.monotonic()-started)
            record_path.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n');reports.append(record)
            print('RUNTIME_FREEZE_VERIFIED='+json.dumps(dict(role=role,files=len(saved),bytes=sum(r.get('bytes',0) for r in saved)),ensure_ascii=False),flush=True)
        report=dict(schema='flexifold_runtime_freeze_v1',completed_at=datetime.now().astimezone().isoformat(),passed=True,
                    roles=[r['role'] for r in reports],baseline_commit=subprocess.check_output(['git','rev-parse','baseline-before-fpga-authority-20261114^{commit}'],cwd=ROOT,text=True).strip(),
                    model_and_python_files_copied=True,hardlinks_used=False,caches_excluded=True,
                    system_python_and_drivers_archived=False,board_snapshot_present=False,robot_commands_sent=False)
        (out/'freeze_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print('RUNTIME_FREEZE_DONE='+str(out/'freeze_report.json'),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
