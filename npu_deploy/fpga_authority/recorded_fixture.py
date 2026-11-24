#!/usr/bin/env python3
"""Extract logged SDK state and nearby recorded camera-cache frames; no devices."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

PROJECT=Path(__file__).resolve().parents[2]


def digest(path):
    with Path(path).open('rb') as source:return hashlib.file_digest(source,'sha256').hexdigest()


def extract(recording,out,trace_path=None):
    import cv2
    from PIL import Image
    recording=Path(recording).resolve();out=Path(out);out.mkdir(parents=True,exist_ok=True)
    if (out/'observation.json').exists():raise FileExistsError('Retain fixture provenance; choose a new output directory')
    observations=[json.loads(line) for line in (recording/'robot_events.jsonl').open() if line.strip()]
    observations=[row for row in observations if row.get('kind')=='observation']
    trace_path=Path(trace_path).resolve() if trace_path is not None else recording/'policy_trace.json'
    trace=json.loads(trace_path.read_text());target=trace['rows'][0]['tick_monotonic_s']
    observation=min(observations,key=lambda row:abs(row['monotonic_s']-target))
    stamps=[json.loads(line) for line in (recording/'frame_times.jsonl').open() if line.strip()]
    older=[row for row in stamps if row['monotonic_s']<=observation['monotonic_s']]
    if not older:raise ValueError('No camera-cache sample at or before the logged observation')
    stamp=max(older,key=lambda row:row['monotonic_s']);images={};videos={}
    for role in ('left','middle','right'):
        path=recording/(role+'.avi')
        if not path.is_file():raise ValueError('Missing recorded camera video: '+str(path))
        cap=cv2.VideoCapture(str(path));cap.set(cv2.CAP_PROP_POS_FRAMES,stamp['video_frame_index']);ok,bgr=cap.read();cap.release()
        if not ok:raise ValueError('Video frame cannot be decoded: '+role)
        name=role+'.png';Image.fromarray(cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)).save(out/name);images[role]=name;videos[role]=dict(path=str(path),sha256=digest(path))
    packaged=json.loads((PROJECT/'amd_hybrid_deploy_20261031/fixtures/observation.json').read_text())
    report=dict(source_kind='RECORDED_SDK_STATE_AND_CAMERA_CACHE',feature_names=observation['feature_names'],state=observation['state'],
        task=packaged['task'],images=images,qualification='Logged 28-state observation and preceding sampled video frame, not atomically synchronized sensor exposures or original tensor capture.',
        provenance=dict(recording=str(recording),observation_monotonic_s=observation['monotonic_s'],camera_cache_monotonic_s=stamp['monotonic_s'],
            state_cache_gap_ms=(observation['monotonic_s']-stamp['monotonic_s'])*1000,video_frame_index=stamp['video_frame_index'],
            robot_events_sha256=digest(recording/'robot_events.jsonl'),frame_times_sha256=digest(recording/'frame_times.jsonl'),videos=videos,
            trace_path=str(trace_path),trace_sha256=digest(trace_path),
            task_source='existing packaged benchmark prompt',image_sha256={role:digest(out/name) for role,name in images.items()}))
    if len(report['feature_names'])!=28 or len(set(report['feature_names']))!=28:raise ValueError('Incomplete recorded state')
    (out/'observation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--recording',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--trace',type=Path,help='Explicit trace when the launcher saved it outside the recording directory')
    args=p.parse_args();r=extract(args.recording,args.out,args.trace);print(json.dumps(dict(fixture=str(args.out/'observation.json'),source=r['source_kind'],cache_gap_ms=r['provenance']['state_cache_gap_ms'])));return 0


if __name__=='__main__':raise SystemExit(main())
