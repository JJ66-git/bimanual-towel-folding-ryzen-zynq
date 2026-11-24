#!/usr/bin/env python3
"""Recorded-only opening experiment; outputs scalar proposals, never actions.

The approach window is manually annotated. It cannot authorize closing or
recognize a live phase. The unchanged FPGA reference must reject an opening
that differs from the recorded ACT-owned gripper: report that incompatibility.
"""
import argparse
import json
from pathlib import Path
import sys

import numpy as np


def propose(rows, annotation):
    start, end = annotation['start_step'], annotation['end_step']
    floor = float(annotation['opening_floor_mm']) / 1000
    speed = float(annotation['slew_mm_per_s']) / 1000
    if not (0 < start <= end < len(rows) and 0 < floor <= .08 and 0 < speed <= .2):
        raise ValueError('Invalid offline annotation or opening range')
    previous = rows[start-1]['target'][13]
    results = []
    for i in range(start, end+1):
        row = rows[i]
        dt = row['tick_monotonic_s'] - rows[i-1]['tick_monotonic_s']
        if not 0 < dt < 1:
            raise ValueError('Recorded timing gap cannot support this slew experiment')
        desired = max(row['target'][13], floor)
        value = previous + float(np.clip(desired-previous, -speed*dt, speed*dt))
        if not 0 <= value <= .08 or abs(value-previous) > speed*dt+1e-9:
            raise AssertionError('Offline proposal envelope violated')
        results.append(dict(step=row['step'], recorded_target_mm=row['target'][13]*1000,
                            right_opening_proposal_mm=value*1000, delta_from_recorded_mm=(value-row['target'][13])*1000,
                            elapsed_s=row['tick_monotonic_s']-rows[0]['tick_monotonic_s']))
        previous = value
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace',type=Path,required=True)
    parser.add_argument('--annotation',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--plot-library',type=Path)
    args=parser.parse_args()
    annotation=json.loads(args.annotation.read_text());rows=json.loads(args.trace.read_text())['rows']
    if annotation.get('enabled_for_execution') is not False or annotation.get('phase_detector_available') is not False:
        raise ValueError('Only explicitly disabled, manually annotated experiments are accepted')
    results=propose(rows,annotation)
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root/'fpga_integration/fpga_code/fpga_workspace'))
    from host.protocol import Sample,RUNNING,CHECK_CORRECTION,UNKNOWN,quantize
    from host.reference import load_profile,profile_crc,evaluate
    profile=load_profile();rejected=0
    for proposed in results:
        row=rows[proposed['step']];target=list(row['target']);target[13]=proposed['right_opening_proposal_mm']/1000
        sample=Sample(profile_crc(profile),row['step'],RUNNING|CHECK_CORRECTION,
            int(proposed['elapsed_s']*1e6),UNKNOWN,0,quantize(row['measured']),quantize(row['previous']),
            quantize(row['act_action']),quantize(target),quantize(row['correction']),quantize(row['correction']))
        check=evaluate(sample,profile)
        proposed['unchanged_reference_violations']=check['names']
        rejected+=bool(check['violations'])
    args.out.mkdir(parents=True,exist_ok=False)
    (args.out/'scalar_proposals.json').write_text(json.dumps(results,indent=2)+'\n')
    if args.plot_library:sys.path.append(str(args.plot_library))
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    fig,axis=plt.subplots(figsize=(12,4),constrained_layout=True)
    axis.plot([row['step'] for row in rows],[row['target'][13]*1000 for row in rows],label='recorded ACT target',color='#28623d')
    axis.plot([row['step'] for row in results],[row['right_opening_proposal_mm'] for row in results],label='manual-window proposal, not executed',color='#c0702d')
    axis.set(xlabel='recorded step',ylabel='right opening (mm)',title='Opening-only what-if; closing and robot-space alignment remain unverified')
    axis.legend();axis.grid(alpha=.2);fig.savefig(args.out/'opening_only_what_if.png',dpi=150);plt.close(fig)
    report=dict(schema='offline_right_opening_proposal_v1',annotation=annotation,proposals=len(results),
        robot_commands_sent=False,live_cameras_opened=False,production_policy_changed=False,
        eligible_for_execution=False,closing_authorized=False,joint_targets_exported=False,
        unchanged_reference_rejections=rejected,reference_is_real_pl=False,
        expected_contract_conflict='Current FPGA contract requires target gripper == original ACT gripper. A phase supervisor cannot be inserted without explicitly versioning the primary-command semantics and preserving raw model outputs.',
        limitations=['Changing recorded openings also changes future images, contacts and model states; this replay cannot predict task success.',
                     'The manual step window is an analysis annotation, never an online phase trigger.',
                     'No camera-to-robot calibration or right-edge closed-loop correction has been established.'])
    (args.out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(out=str(args.out),proposals=len(results),unchanged_reference_rejections=rejected,eligible_for_execution=False,robot_commands_sent=False)))


if __name__=='__main__':main()
