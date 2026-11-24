"""Join PC/board JSONL by session and sequence; clocks are never subtracted across devices."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import statistics

FCLK_HZ = 50_000_000


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def make_report(pc_path, board_path, output):
    pc = read_rows(pc_path)
    board = read_rows(board_path)
    exchanges = [row for row in pc if row['event'] == 'diagnostic_exchange']
    if not exchanges:
        raise ValueError('No diagnostic exchanges')
    paired_events = {'session_started','status','action_observed','monitor_paused','monitor_resumed','session_ended'}
    indexed = {}
    for row in board:
        if row['event'] in paired_events:
            key = row['session'], row['sequence']
            if key in indexed:
                raise ValueError('Duplicate board exchange identity')
            indexed[key] = row
    merged = []
    for row in exchanges:
        other = indexed.get((row['session'], row['sequence']))
        if other is None or other['sender_tick_us'] != row['sender_tick_us']:
            raise ValueError('Missing or mismatched board exchange')
        if any(row['response'][name] != other['watchdog'][name] for name in other['watchdog']):
            raise ValueError('PC and board response values differ')
        if any(row['response'][name] != other[name] for name in ('violations','events','phase')):
            raise ValueError('PC and board checker values differ')
        merged.append(dict(label=row['label'],session=row['session'],sequence=row['sequence'],
            operation=row['operation'],host_sent_ns=row['host_sent_ns'],host_received_ns=row['host_received_ns'],
            host_round_trip_us=row['host_round_trip_us'],board_received_ns=other['board_received_ns'],
            commit_written=other.get('commit_written',False),sample_step=other.get('sample_step',''),
            board_commit_before_ns=other.get('commit_before_ns',''),board_commit_after_ns=other.get('commit_after_ns',''),
            board_check_us=(other['pl_check_done_ns']-other['pl_check_started_ns'])/1000 if 'pl_check_done_ns' in other else '',
            watchdog_status=row['response']['status'],timeout_count=row['response']['timeout_count'],
            watchdog_age_ms=row['response']['age_cycles']*1000/FCLK_HZ,
            raw_last_gap_cycles=row['response']['last_gap_cycles']))
    counts = Counter(row['event'] for row in board)
    summary = dict(paired_exchanges=len(merged), input_sources=sorted({row['input_source'] for row in pc}),
        timeout_events=counts['watchdog_expired'], action_events=counts['action_observed'],
        commits_written=sum(row['commit_written'] for row in merged),
        rejected_packets=counts['packet_rejected'], fpg1_checks=counts['check_reply_sent'],
        pc_round_trip_us=dict(median=statistics.median(row['host_round_trip_us'] for row in merged),
                              max=max(row['host_round_trip_us'] for row in merged)),
        final_watchdog_status=merged[-1]['watchdog_status'], final_timeout_count=merged[-1]['timeout_count'],
        fclk_hz=FCLK_HZ, raw_last_gap_is_latched=True, robot_execution_verified=False)
    output.mkdir(parents=True,exist_ok=False)
    with (output/'events.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(merged[0]))
        writer.writeheader()
        writer.writerows(merged)
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pc',type=Path,required=True)
    parser.add_argument('--board',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(make_report(args.pc,args.board,args.out),indent=2))


if __name__ == '__main__':
    main()
