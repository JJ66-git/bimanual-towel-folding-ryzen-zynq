"""Render recorded PL results; no hardware access or robot control."""
import argparse
import json
from pathlib import Path

from screen import view


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    metadata=args.out.with_suffix('.json')
    if args.out.exists() or metadata.exists():
        parser.error('output files must be new')
    state=view.read_state(args.log)
    manifest=json.loads(args.manifest.read_text(encoding='utf-8-sig'))
    args.out.parent.mkdir(parents=True,exist_ok=True)
    view.render(state,'REPLAY',manifest.get('input_source','unknown')).save(args.out)
    metadata.write_text(json.dumps(dict(state.summary(),display_mode='REPLAY',physical_display_verified=False),indent=2)+'\n',encoding='utf-8')
    print('Rendered offline replay:',args.out)


if __name__ == '__main__':
    main()
