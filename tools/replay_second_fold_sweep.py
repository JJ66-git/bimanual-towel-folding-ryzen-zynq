#!/usr/bin/env python3
"""Replay the loaded-but-stalled guard on labeled archives, with no hardware I/O."""

import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np

from replay_first_grasp import POLICY
sys.path.insert(0, str(POLICY / 'deployment_entry'))

from fold_handover import FoldHandoverCheck


def replay(run):
    step = run['regrasp_step']
    if step is None:
        return None
    trace = json.loads(Path(run['trace']).read_text())
    rows = trace['rows']
    monitor = FoldHandoverCheck()
    state = SimpleNamespace(first_fold={}, regrasp={'step':step})
    for row in rows[step:]:
        t = row['tick_monotonic_s']
        monitor.observe(t, row['step'], state, np.asarray(row['previous']),
                         row.get('gripper_feedback_before_action') or {},
                         measured=np.asarray(row['measured']),
                         time_left_s=(len(rows)-row['step'])/trace.get('fps',30.))
        if monitor.sweep_stalled is not None:
            return monitor.sweep_stalled
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recording-audit', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.recording_audit.read_text())
    results = [
        dict(name=run['name'], label=run['label'],
             confirmed_regrasp=run['regrasp_step'], recovery_candidate=replay(run))
        for run in data['runs']
    ]
    args.out.write_text(json.dumps(dict(results=results, robot_commands_sent=False,
        scope='Recorded pose, gripper feedback and labeled first fold. No alternate motion was sent.'),
        indent=2)+'\n')
    print([(r['name'],r['recovery_candidate']['step']) for r in results if r['recovery_candidate']])
    assert not any(r['recovery_candidate'] for r in results if r['label']=='completed')


if __name__ == '__main__':
    main()
