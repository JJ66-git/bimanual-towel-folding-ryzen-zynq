#!/usr/bin/env python3
"""Tune one simulated recovery arc, then evaluate it on separate seeds."""

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import time

import numpy as np

from simulate_second_fold_feedback import build_cases, execute, initialise, read_trajectories


CANDIDATES = (
    (.148, 1.70, .50, 0.),
    (.120, 1.70, .50, 0.),
    (.172, 1.70, .50, 0.),
    (.148, 1.50, .50, 0.),
    (.148, 1.94, .50, 0.),
    (.148, 1.70, .39, 0.),
    (.148, 1.70, .61, 0.),
    (.148, 1.70, .50, 8.),
    (.148, 1.70, .50, -8.),
)


def adapted(scenario, profile):
    lift, seconds, press, beyond = profile
    return replace(scenario, arc_height_m=lift, arc_duration_s=seconds,
                   pad_release_share=press,overtravel_mm=beyond)


def brief(rows):
    return dict(cases=len(rows), folded=sum(r['success'] for r in rows),
                interventions=sum(r['recovery'] is not None for r in rows),
                median_alignment_mm=float(np.median([max(r['final_alignment_mm']) for r in rows])),
                p90_alignment_mm=float(np.percentile([max(r['final_alignment_mm']) for r in rows],90)),
                p95_constraint_force_n=float(np.percentile([r['peak_constraint_load_n'] for r in rows],95)))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=8)
    args=parser.parse_args()
    all_runs=read_trajectories(args.audit,include_orientation=True)
    focused={k:v for k,v in all_runs.items() if k=='current_1021' or v['label']=='completed'}
    all_training=list(build_cases(focused,'tuning',9,13,881120,'settled','recorded'))
    training=[replace(r,recording='current_1021',fault='none') for r in all_training[:6]]
    validation=list(build_cases(focused,'holdout',24,17,411201,'settled','recorded'))
    validation=[replace(r,first_fold_setup='rolled' if i%4==0 else 'settled')
                for i,r in enumerate(validation)]
    t0=time.perf_counter()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    journal=args.out.with_suffix('.jsonl')
    with ProcessPoolExecutor(args.workers,initializer=initialise,initargs=(all_runs,)) as pool, \
         journal.open('w',buffering=1) as sink:
        ranks=[]
        for profile in CANDIDATES:
            results=list(pool.map(execute,[(adapted(r,profile),'recovery_arc') for r in training]))
            outcome=brief(results)
            for row in results:
                sink.write(json.dumps(dict(split='tuning',parameters=profile,**row))+'\n')
            ranks.append(dict(parameters=profile, metrics=outcome))
            print(profile,outcome,flush=True)
        winner=min(ranks,key=lambda r:(-r['metrics']['folded'],r['metrics']['p90_alignment_mm'],
                                       r['metrics']['p95_constraint_force_n']))
        print('SELECTED',winner,flush=True)
        jobs=[job for row in validation for job in
              ((row,'recorded'),(row,'recovery_arc'),(adapted(row,winner['parameters']),'recovery_arc'))]
        paired=[]
        for i,result in enumerate(pool.map(execute,jobs)):
            key=('recorded','untuned','tuned')[i%3]
            paired.append(dict(comparison=key,**result))
            sink.write(json.dumps(dict(split='holdout',**paired[-1]))+'\n')
            if (i+1)%12==0:
                print(f'Holdout {i+1}/{len(jobs)}; {time.perf_counter()-t0:.1f}s wall',flush=True)
    summary={}
    for key in ('recorded','untuned','tuned'):
        rows=[r for r in paired if r['comparison']==key]
        summary[key]=brief(rows)
        summary[key]['normal']=brief([r for r in rows if r['case']['fault']=='none'])
        summary[key]['originally_failed_normal']=brief(
            [r for r in rows if r['case']['fault']=='none' and r['field_outcome']=='loaded_incomplete'])
        assert not any(r['recovery_motion'] for r in rows if r['case']['fault'] in ('left_loaded','empty','stale'))
    args.out.write_text(json.dumps(dict(robot_commands_sent=False,tuning_seed=881120,validation_seed=411201,
        tuning=ranks,selected=winner,validation=summary,wall_seconds=time.perf_counter()-t0,
        results=paired),indent=2,allow_nan=False)+'\n')
    print(summary,flush=True)


if __name__=='__main__':
    main()
