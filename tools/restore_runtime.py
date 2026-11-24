#!/usr/bin/env python3
"""Verify and restore frozen runtime roles; default is a read-only plan.

Each new tree is fully copied and hash-checked before replacement. Existing
trees are retained under .pre_restore names. Never activates or installs Python.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid
import sys

spec=importlib.util.spec_from_file_location('runtime_freeze_inventory',Path(__file__).with_name('freeze_runtime.py'))
freeze=importlib.util.module_from_spec(spec);spec.loader.exec_module(freeze)


def plan(snapshot,source_workspace,destination_workspace,roles=None):
    snapshot=Path(snapshot).resolve();source_workspace=Path(source_workspace).resolve();destination_workspace=Path(destination_workspace).resolve()
    report=json.loads((snapshot/'freeze_report.json').read_text())
    if report.get('passed') is not True or report.get('schema')!='flexifold_runtime_freeze_v1':raise ValueError('Unverified runtime snapshot')
    selected=report['roles'] if roles is None else roles
    if len(set(selected))!=len(selected) or any(role not in report['roles'] for role in selected):raise ValueError('Unknown or duplicate runtime role')
    rows=[]
    for role in selected:
        if Path(role).name!=role:raise ValueError('Invalid role path')
        record=json.loads((snapshot/(role+'.manifest.json')).read_text());backup=snapshot/role
        if record['role']!=role or freeze.inventory(backup)!=record['files']:raise ValueError('Runtime snapshot integrity failed: '+role)
        relative=Path(record['source']).relative_to(source_workspace)
        target=destination_workspace/relative
        if '..' in relative.parts or target==destination_workspace:raise ValueError('Invalid runtime destination')
        rows.append(dict(role=role,backup=str(backup),destination=str(target),files=record['files'],
            bytes=sum(f.get('bytes',0) for f in record['files'])))
    destinations=[Path(row['destination']) for row in rows]
    if any(a!=b and (a in b.parents or b in a.parents) for a in destinations for b in destinations):raise ValueError('Overlapping runtime destinations')
    if any(snapshot==d or snapshot in d.parents or d in snapshot.parents for d in destinations):raise ValueError('Never replace the snapshot or its parents')
    return rows


def restore(rows):
    staged=[];committed=[]
    try:
        for row in rows:
            target=Path(row['destination']);target.parent.mkdir(parents=True,exist_ok=True)
            stage=Path(tempfile.mkdtemp(prefix='.runtime_restore_',dir=target.parent))/'payload'
            staged.append((row,stage))
            source=Path(row['backup'])
            if source.is_dir():shutil.copytree(source,stage,symlinks=True)
            else:shutil.copy2(source,stage,follow_symlinks=False)
            if freeze.inventory(stage)!=row['files']:raise RuntimeError('Staged copy differs from manifest: '+row['role'])
        for row,stage in staged:
            target=Path(row['destination']);previous=None
            if os.path.lexists(target):
                previous=target.with_name(target.name+'.pre_restore_'+uuid.uuid4().hex);target.rename(previous)
            try:stage.rename(target)
            except BaseException:
                if previous:previous.rename(target)
                raise
            committed.append(dict(role=row['role'],destination=str(target),retained_previous=str(previous) if previous else None))
        return dict(passed=True,restored=committed,hardlinks_used=False,robot_commands_sent=False)
    except BaseException:
        for row in reversed(committed):
            target=Path(row['destination']);target.rename(target.with_name(target.name+'.failed_restore_'+uuid.uuid4().hex))
            if row['retained_previous']:Path(row['retained_previous']).rename(target)
        raise
    finally:
        for _,stage in staged:
            if stage.exists():
                if stage.is_dir():shutil.rmtree(stage)
                else:stage.unlink()
            stage.parent.rmdir()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--snapshot',type=Path,required=True)
    p.add_argument('--source-workspace',type=Path,required=True);p.add_argument('--destination-workspace',type=Path)
    p.add_argument('--role',action='append');p.add_argument('--apply',action='store_true');args=p.parse_args()
    rows=plan(args.snapshot,args.source_workspace,args.destination_workspace or args.source_workspace,args.role)
    if args.apply:
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'npu_deploy'))
        from operation_lock_path import operation_lock
        with operation_lock():result=restore(rows)
    else:result=dict(mode='read_only_plan',passed=True,roles=[{k:v for k,v in row.items() if k!='files'} for row in rows],system_drivers_archived=False,robot_commands_sent=False)
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
