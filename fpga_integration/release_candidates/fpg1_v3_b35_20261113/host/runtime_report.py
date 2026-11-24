"""Export observed task, display and COMMIT gaps; none are robot control periods."""
import argparse
from collections import Counter
import csv
import json
import math
from pathlib import Path
import statistics


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def stats(values):
    if not values:return None
    ordered=sorted(values)
    return dict(count=len(values),median=statistics.median(values),
                p95=ordered[math.ceil(len(ordered)*0.95)-1],max=max(values))


def write_csv(path,data,fields):
    with path.open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore')
        writer.writeheader();writer.writerows(data)


def make_report(directory,out):
    board=rows(directory/'board.jsonl');frames=rows(directory/'frames.jsonl')
    tasks=[row for row in board if row['event']=='task_status_received']
    rendered=[]
    for row in frames:
        watchdog=row['watchdog'] or {}
        rendered.append(dict(board_perf_ns=row['board_perf_ns'],generation=row['generation'],
            checked=row['checked'],render_us=row['render_us'],present_us=row['present_us'],
            watchdog_status=watchdog.get('status'),timeout_count=watchdog.get('timeout_count'),
            phase=(row['task_status'] or {}).get('phase')))
    gaps=[];previous=None
    for row in board:
        if row['event'] in ('session_started','monitor_paused','monitor_resumed','session_ended','server_stopped'):
            previous=None
        if row['event']=='action_observed' and row['commit_written']:
            if previous is not None:
                gaps.append(dict(session=row['session'],sequence=row['sequence'],
                    observed_commit_gap_ms=(row['commit_before_ns']-previous)/1e6))
            previous=row['commit_before_ns']
    counts=Counter(row['event'] for row in board)
    summary=dict(display_frames=len(frames),task_claims=len(tasks),checks=counts['action_observed']+counts['check_reply_sent'],
        watchdog_timeout_events=counts['watchdog_expired'],rejected_packets=counts['packet_rejected'],
        display_render_us=stats([r['render_us'] for r in frames]),
        framebuffer_present_us=stats([r['present_us'] for r in frames]),
        display_interval_ms=stats([(b['board_perf_ns']-a['board_perf_ns'])/1e6 for a,b in zip(frames,frames[1:])]),
        observed_commit_gap_ms=stats([g['observed_commit_gap_ms'] for g in gaps]),
        input_sources=sorted({row.get('input_source','unknown') for row in board}),
        task_phases_are_sender_claims=True,production_tick_period_measured=False,
        physical_display_verified=False)
    out.mkdir(parents=True,exist_ok=False)
    write_csv(out/'frames.csv',rendered,['board_perf_ns','generation','checked','render_us','present_us','watchdog_status','timeout_count','phase'])
    write_csv(out/'task_claims.csv',tasks,['session','sequence','sender_tick_us','board_received_ns','origin','phase','activity','claim_only'])
    write_csv(out/'commit_gaps.csv',gaps,['session','sequence','observed_commit_gap_ms'])
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(make_report(args.run,args.out),indent=2))


if __name__=='__main__':main()
