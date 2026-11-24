#!/usr/bin/env python3
"""Whole-run historical visual audit; no frame-wise train/test leakage.

The one available successful robot-camera video supplies a goal template. Four
operator-labelled failures remain held out. This cannot measure held-out success
recall, and no recorded frame supplies verified PL-bound retreat evidence.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

PROJECT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(PROJECT/'amd_hybrid_deploy_20261031/piper_towel_replica/deployment_entry'))
from towel_quality import cloth_features,TowelQualityMonitor
from visual_evidence import VisualEvidenceGate


def digest(path):
    with Path(path).open('rb') as source:return hashlib.file_digest(source,'sha256').hexdigest()


def dataset(source,field_manifest=None):
    source=Path(source);package=source/'local_environment_record/success_reference_incoming/FlexiFold_实机任务证据包_20261114'
    comparison=json.loads((source/'local_environment_record/task_failure_review_20261114/success_failure_comparison.json').read_text())
    runs=[];missing=[];logs=source/'bimanual-towel-folding-ryzen-zynq/amd_hybrid_deploy_20261031/piper_towel_replica/logs'
    recordings={}
    for path in logs.glob('*_task_recording/launch.json'):
        launch=json.loads(path.read_text());args=launch['arguments']
        if '--trace-file' in args:recordings[Path(args[args.index('--trace-file')+1]).parent.name]=path.parent
    for row in comparison['runs']:
        if row['label']=='success_oct30':recording=package/'01_成功_20261030_134006/20261030_134006_220631_task_recording'
        elif row['label']=='success_nov02':recording=package/'02_成功_20261102_2029/task_logs_20261102_2029/ai_pc/task_recording'
        else:recording=recordings.get(row['label'])
        if recording is None or not (recording/'middle.avi').is_file():missing.append(dict(label=row['label'],reason='robot-camera video missing'));continue
        runs.append(dict(label=row['label'],recording=recording,split='calibration' if row['label']=='success_oct30' else 'held_out_failure',
            outcome='success' if row['label'].startswith('success') else 'failure',outcome_source=row['task_outcome_source']))
    profile=package/'02_成功_20261102_2029/runtime_snapshot/piper_towel_replica/configs/cobot_quality_candidate.json'
    if field_manifest is not None:
        field=json.loads(Path(field_manifest).read_text())
        if field.get('schema')!='field_visual_runs_v1':raise ValueError('Explicit field run manifest required')
        known={r['recording'].resolve() for r in runs};labels={r['label'] for r in runs}
        for row in field['runs']:
            recording=Path(row['recording']).resolve();label=row['label'];outcome=row['outcome']
            if (outcome not in ('success','failure') or not row.get('outcome_source')
                    or row.get('source_kind')!='CURRENT_ROBOT_CAMERA_RECORDING'
                    or not label or any(not(c.isascii() and (c.isalnum() or c in '_-')) for c in label)):
                raise ValueError('Field run requires an operator outcome and a safe unique label')
            if recording in known or label in labels:raise ValueError('Duplicate run would leak across visual splits')
            if not all((recording/(role+'.avi')).is_file() for role in ('left','middle','right')):
                raise ValueError('Field audit requires the three original robot-camera videos')
            known.add(recording);labels.add(label)
            runs.append(dict(label=label,recording=recording,split='held_out_'+outcome,
                outcome=outcome,outcome_source=row['outcome_source'],control_session=row.get('control_session')))
    return runs,missing,profile


def frames(recording):
    import cv2
    path=recording/'middle.avi'
    if not path.is_file():raise ValueError('Only regular recorded video files are accepted')
    stamps={r['video_frame_index']:r for r in map(json.loads,(recording/'frame_times.jsonl').read_text().splitlines())}
    cap=cv2.VideoCapture(str(path));index=0
    try:
        while True:
            ok,bgr=cap.read()
            if not ok:break
            if index not in stamps:raise ValueError('Missing recorded frame timestamp')
            yield index,stamps[index]['monotonic_s'],cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
            index+=1
    finally:cap.release()


def main():
    import cv2
    import numpy as np
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source-workspace',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--field-runs',type=Path,help='Additional operator-labelled whole runs, always held out from calibration')
    args=p.parse_args();args.out.mkdir(parents=True,exist_ok=True);runs,missing,legacy_path=dataset(args.source_workspace,args.field_runs)
    legacy=json.loads(legacy_path.read_text());calibration=next(row for row in runs if row['split']=='calibration')
    calibration_frames=list(frames(calibration['recording']));first_feature=cloth_features(calibration_frames[0][2],legacy['segmentation'])
    if first_feature is None:raise RuntimeError('Calibration start cloth is not visible')
    last=calibration_frames[-1];goal=cloth_features(last[2],legacy['segmentation'])
    if goal is None:raise RuntimeError('Calibration terminal cloth is not visible')
    cv2.imwrite(str(args.out/'goal_mask.png'),goal['mask']);cv2.imwrite(str(args.out/'goal_rgb.png'),cv2.cvtColor(last[2],cv2.COLOR_RGB2BGR))
    (args.out/'candidate_profile.json').write_text(json.dumps(dict(schema='offline_goal_geometry_candidate_v1',field_validated=False,
        template_source=calibration['label'],frame_id=last[0],template_selection='last recorded frame of calibration run; no held-out data used',
        template_iou_min=.70,area_ratio_range=[.25,.48],segmentation=legacy['segmentation'],automatic_stop_enabled=False),indent=2)+'\n')
    summaries=[]
    for run in runs:
        gate=VisualEvidenceGate();gate.begin(1,1);legacy_monitor=TowelQualityMonitor(legacy_path);rows=[];start=None;baseline=None
        for index,stamp,rgb in frames(run['recording']):
            if start is None:
                start=stamp;legacy_monitor.check_start(rgb,enforce=False);f=cloth_features(rgb,legacy['segmentation']);baseline=f['area'] if f else None
            f=cloth_features(rgb,legacy['segmentation']);iou=ratio=None;matches=False
            if f is not None and baseline:
                intersection=np.count_nonzero((f['mask']>0)&(goal['mask']>0));union=np.count_nonzero((f['mask']>0)|(goal['mask']>0))
                iou=float(intersection/max(union,1));ratio=f['area']/baseline
                matches=iou>=.70 and .25<=ratio<=.48 and f['rectangularity']>=.82 and f['solidity']>=.90 and f['dark_fraction']<=.065
            evidence=gate.observe(session=1,event=1,frame_id=index,captured_ns=int(stamp*1e9),now_ns=int(stamp*1e9),
                retreat_ready=None,visible=f is not None,geometry_matches=matches,center=f['center'] if f else None,area=f['area'] if f else None)
            # Identical replay timestamp means zero simulated transport age;
            # it is not a measured historical sensor freshness value.
            legacy_stop=legacy_monitor.update(rgb,stamp-start)
            rows.append(dict(frame=index,elapsed_s=stamp-start,visible=f is not None,goal_iou=iou,area_ratio=ratio,geometry_candidate=bool(matches),
                legacy_heuristic_stop=legacy_stop,evidence=evidence))
        rowfile=args.out/(run['label']+'.json');rowfile.write_text(json.dumps(rows,indent=2)+'\n')
        summaries.append(dict(label=run['label'],split=run['split'],outcome=run['outcome'],outcome_source=run['outcome_source'],
            video=str(run['recording']/'middle.avi'),video_sha256=digest(run['recording']/'middle.avi'),frames=len(rows),
            geometry_candidate_frames=sum(row['geometry_candidate'] for row in rows),legacy_stop_ever=any(row['legacy_heuristic_stop'] for row in rows),
            verified_completion_frames=0,unknown_retreat_frames=len(rows),first_geometry_candidate_s=next((row['elapsed_s'] for row in rows if row['geometry_candidate']),None),
            rows=str(rowfile)))
    report=dict(schema='recorded_visual_evidence_audit_v1',passed=True,runs=summaries,missing=missing,
        held_out_success_runs=sum(row['split']=='held_out_success' for row in summaries),held_out_failure_runs=sum(row['split']=='held_out_failure' for row in summaries),
        held_out_failures_with_geometry_candidates=sum(row['split']=='held_out_failure' and row['geometry_candidate_frames']>0 for row in summaries),
        automatic_stop_enabled=False,task_success_certified=False,robot_commands_sent=False,
        replay_identity='synthetic harness identity, not a recorded PL event',frame_age='zero simulated replay transport age, not historical exposure freshness',
        limitations=['A historical success supplies calibration; additional successes, if supplied, are whole-run held-out observations and do not establish broad success recall.',
            'Recorded inputs have run-level operator labels, not per-frame fold/layer or PL-bound retreat annotations.',
            'Geometry candidates are not fold-success declarations; absent retreat evidence remains UNKNOWN.',
            'Phone partial-completion video is a different camera domain and was not mixed into robot-camera calibration.'])
    (args.out/'visual_replay.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('VISUAL_REPLAY='+json.dumps({k:v for k,v in report.items() if k not in ('runs','limitations')},ensure_ascii=False));return 0


if __name__=='__main__':raise SystemExit(main())
