#!/usr/bin/env python3
"""Build a right-jaw trajectory benchmark from labeled archived towel-fold runs."""

import argparse
import json
from pathlib import Path
import sys

import cv2
import numpy as np

from replay_first_grasp import POLICY

sys.path.insert(0,str(POLICY/'deployment_entry'))
from first_grasp_approach import jaw_pose
from towel_finish import TowelFinishCheck


def geometry(image):
    bgr = cv2.imread(str(image))
    return None if bgr is None else TowelFinishCheck.geometry(cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB))


def audit(run):
    trace = json.loads(Path(run['trace']).read_text())
    rows = trace['rows']
    finish = trace.get('finish_check') or {}
    step = (finish.get('regrasp') or {}).get('step',run.get('regrasp_step'))
    recording = Path(run['recording'])
    initial, final = (geometry(recording/(name+'_middle.png')) for name in ('first','last'))
    item = dict(run, regrasp_step=step,
                final_area_fraction=final['area']/initial['area'] if initial and final else None,
                final_length_fraction=final['length']/initial['length'] if initial and final else None)
    if step is None:
        return item
    offset = next(i for i,row in enumerate(rows) if row['step']==step)
    tail = rows[offset:]
    ts = np.asarray([row['tick_monotonic_s'] for row in tail])
    ts -= ts[0]
    angles = [np.array([row[key][7:13] for row in tail]) for key in ('measured','target','act_action')]
    measured, target, act = [jaw_pose(q)[:,:3] for q in angles]
    for path in (measured,target,act):
        path -= path[0]
    widths = 1000*np.array([row['target'][13] for row in tail])
    feedback = np.array([[g.get('width_mm',0.),g.get('effort_Nm',0.)] for row in tail
                         for g in [(row.get('gripper_feedback_before_action') or {}).get('right') or {}]])
    opens = np.flatnonzero(widths >=18.)
    first_open = int(opens[0]) if opens.size else len(tail)-1
    samples = {}
    for seconds in (1.,1.5,2.,2.5,3.,3.5,4.,4.5,5.,6.):
        last = min(first_open+1,int(np.searchsorted(ts,seconds,side='right')))
        samples[seconds] = [round(float(np.max(path[:last,1])),1) for path in (measured,target,act)]
    item.update(trajectory=dict(elapsed_s=ts.tolist(), measured_jaw_delta_mm=measured.tolist(),
                     target_jaw_delta_mm=target.tolist(),act_jaw_delta_mm=act.tolist(),
                     target_width_mm=widths.tolist(),measured_gripper_width_effort=feedback.tolist()),
                first_target_open_step=tail[first_open]['step'] if opens.size else None,
                raw_peak_before_open_mm=float(np.max(measured[:first_open+1,1])),
                progression_mm=samples,
                peak_joint_error_rad=float(np.max(np.abs(angles[0]-angles[1]))),
                smol_fraction=sum(row['source']=='SmolVLA' for row in tail)/len(tail))
    return item


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    data=json.loads(args.manifest.read_text())
    rows=[audit(run) for run in data['runs']]
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(dict(runs=rows, robot_commands_sent=False,
        scope='Piper SDK local right jaw estimate. Positive local Y corresponds to the observed cross-towel '
              'motion on these recordings. No world-frame, camera, or grip clearance calibration.'),
        indent=2,allow_nan=False)+'\n')
    print(len(rows),'recordings audited.',flush=True)


if __name__=='__main__':
    main()
