#!/usr/bin/env python3
"""Recompute aggregates from separately supplied private run logs."""
import argparse
import json
import math
from pathlib import Path
import statistics


def stats(values):
    values=sorted(float(v) for v in values)
    if not values:return None
    def q(percent):return values[math.ceil(percent*len(values))-1]
    return dict(n=len(values),p50=statistics.median(values),p95=q(.95),p99=q(.99),maximum=max(values))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace',type=Path,required=True)
    parser.add_argument('--checks',type=Path,required=True)
    parser.add_argument('--summary',type=Path,required=True)
    args=parser.parse_args()
    trace=json.loads(args.trace.read_text())
    rows=trace['rows']
    checks=[json.loads(line) for line in args.checks.read_text().splitlines() if line.strip()]
    checks=[row for row in checks if row.get('kind')=='task_sample']
    shadow=json.loads(args.summary.read_text())
    intervals=[(b['tick_monotonic_s']-a['tick_monotonic_s'])*1000 for a,b in zip(rows,rows[1:])]
    output=dict(execute=trace.get('execute'),termination_reason=trace.get('termination_reason'),
        task_outcome='not_inferred',steps=len(rows),tick_interval_ms=stats(intervals),
        act_pipeline_ms=stats([row['act_pipeline_s']*1000 for row in rows]),
        fpga_rtt_ms=stats([row['host_round_trip_us']/1000 for row in checks]),
        board_processing_ms=stats([row['board_processing_us']/1000 for row in checks]),
        fpga_counts={key:shadow.get(key) for key in ('sent','replied','timeouts','checked','reference_matches','reference_mismatches')},
        quantile='p50 median, p95/p99 nearest-rank',physical_response_latency='not_measured')
    print(json.dumps(output,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
