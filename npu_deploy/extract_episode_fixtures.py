#!/usr/bin/env python3
"""Extract a few frame-indexed offline fixtures; never write the source HDF5."""
import argparse
import json
from pathlib import Path

import h5py
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ROLES = {'left': 'cam_left_wrist', 'middle': 'cam_high', 'right': 'cam_right_wrist'}
MOTORS = [*(f'joint_{n}' for n in range(1, 7)), 'gripper']
NAMES = [f'{side}_{motor}.{kind}' for side in ('left', 'right') for motor in MOTORS for kind in ('pos', 'effort')]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=ROOT/'npu_deploy/raw_data/episode_9.hdf5')
    p.add_argument('--out', type=Path, default=ROOT/'npu_deploy/artifacts/episode9/fixtures')
    p.add_argument('--frames', type=int, nargs='+', default=[0, 450, 899])
    args = p.parse_args()
    with h5py.File(args.source, 'r') as f:
        qpos, effort = f['observations/qpos'], f['observations/effort']
        if len(qpos.shape) != 2 or qpos.shape[1] != 14 or effort.shape != qpos.shape:
            raise ValueError('Expected qpos and effort arrays of shape [frames, 14]')
        for role, cam in ROLES.items():
            if f[f'observations/images/{cam}'].shape != (qpos.shape[0], 480, 640, 3):
                raise ValueError(f'Unexpected frame geometry for {role}: {cam}')
        for index in args.frames:
            if not 0 <= index < qpos.shape[0]:
                raise ValueError(f'Frame index out of range: {index}')
        args.out.mkdir(parents=True, exist_ok=True)
        for index in args.frames:
            folder = args.out / f'frame_{index:04d}'
            folder.mkdir(exist_ok=True)
            q = np.asarray(qpos[index], dtype=np.float32)
            e = np.asarray(effort[index], dtype=np.float32)
            if not np.isfinite(q).all() or not np.isfinite(e).all():
                raise ValueError(f'Non-finite state in frame {index}')
            state = np.stack((q, e), axis=1).reshape(-1).tolist()
            images = {}
            for role, cam in ROLES.items():
                name = role + '.png'
                Image.fromarray(f[f'observations/images/{cam}'][index]).save(folder/name)
                images[role] = name
            data = {'schema': 'own-hybrid-fixed-observation-v1', 'source_file': str(args.source.resolve()),
                    'source_frame': index, 'source_kind': 'HDF5 same-index samples; timestamps absent. Camera and 14D state order assumed from names and project convention; raw values unchanged.',
                    'camera_mapping_assumed': ROLES, 'task': 'Fold the towel with both Piper arms.',
                    'feature_names': NAMES, 'state': state, 'images': images}
            (folder/'observation.json').write_text(json.dumps(data, indent=2, allow_nan=False)+'\n')
            print('FIXTURE=' + str(folder/'observation.json'), flush=True)


if __name__ == '__main__':
    main()
