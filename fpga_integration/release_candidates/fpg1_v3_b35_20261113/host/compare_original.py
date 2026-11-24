"""Compare recorded checks with the actual snapshotted Python functions, offline.

Requires the existing NumPy installation, but never imports a robot or policy.
Only validate_execution is extracted from the otherwise hardware-importing entry.
"""
import argparse
import ast
import json
from pathlib import Path
import runpy
from types import SimpleNamespace


def compare():
    import numpy as np
    from .reference import evaluate, load_profile
    from .replay import samples
    root=Path(__file__).resolve().parents[1]
    source=root/'source_snapshot/piper_towel_replica/deployment_entry'
    core=runpy.run_path(str(source/'blend_core.py'))
    motion=json.loads((root/'source_snapshot/piper_towel_replica/configs/cobot_motion.json').read_text())
    core['LOWER'][:]=motion['software_lower']
    core['UPPER'][:]=motion['software_upper']
    tree=ast.parse((source/'cobot_hybrid.py').read_bytes())
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='validate_execution')
    namespace=dict(np=np,argparse=argparse,ARM=core['ARM'],max_parts=core['max_parts'],validate_absolute=core['validate_absolute'])
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(source/'cobot_hybrid.py'),'exec'),namespace)
    args=SimpleNamespace(act_joint_step_limit=1.0,act_tracking_limit=3.0,correction_step_limit=0.02)
    profile=load_profile()
    count=0; differences=[]
    for path in sorted((root/'examples').glob('real_trace*.json')):
        trace=json.loads(path.read_text())
        old_correction=np.zeros(14,dtype=np.float32)
        for (sequence,sample),row in zip(samples(trace,profile),trace['rows']):
            vectors={key:np.asarray(row[key],dtype=np.float32) for key in
                     ('target','act_action','measured','previous','correction')}
            original=0
            for key,bit in (('target',0),('act_action',1)):
                try:core['validate_absolute'](vectors[key])
                except RuntimeError:original |= 1<<bit
            try:
                namespace['validate_execution'](vectors['target'],vectors['measured'],vectors['previous'],args,
                    trusted_act=not bool(sample.flags&2),correction=vectors['correction'],previous_correction=old_correction)
            except RuntimeError as error:
                message=str(error)
                if 'joint step' in message:original |= 1<<2
                elif 'tracking' in message:original |= 1<<3
                elif 'correction step' in message:original |= 1<<5
                elif not original & 1:raise
            old_correction=vectors['correction']
            integer=evaluate(sample,profile)['violations'] & 0x2f
            if integer != original:
                differences.append(dict(trace=path.name,step=row['step'],integer=integer,original=original))
            count+=1
    return dict(rows=count,differences=differences,passed=not differences,
        scope='Existing target/ACT absolute bounds, joint step, tracking, correction slew on recorded rows',
        limitation='Not a proof of float32 equivalence for all values; boundary rounding differs',
        hardware_access=False)


if __name__=='__main__':
    result=compare()
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if result['passed'] else 1)
