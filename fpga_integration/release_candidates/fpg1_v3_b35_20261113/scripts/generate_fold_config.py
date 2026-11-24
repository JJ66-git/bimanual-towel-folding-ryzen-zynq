#!/usr/bin/env python3
"""Generate or check the exact JSON identity and RTL constants before synthesis."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from host.fold_config import load,identity,header
p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');args=p.parse_args()
profile=load();items={ROOT/'rtl/fold_verify_params.svh':header(profile),ROOT/'config/fold_verify_identity.json':json.dumps(identity(profile),sort_keys=True,indent=2)+'\n'}
for path,value in items.items():
    if args.check:
        if not path.exists() or path.read_text()!=value:raise SystemExit('STALE_CONFIG_ARTIFACT='+str(path))
    else:path.write_text(value)
print('PHASE_CONFIG_IDENTITY='+json.dumps(identity(profile)))
