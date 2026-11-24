"""Audit historical observer evidence; no network, CAN or model execution."""
import collections
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
RUNTIME=ROOT/'fpga_integration/fpga_code/fpga_workspace'
OUT=ROOT.parent/'local_environment_record/fpga_authority_research_20261114'


def percentile(values,p):
    values=sorted(values);i=(len(values)-1)*p/100;low=int(i);high=min(low+1,len(values)-1)
    return values[low]+(values[high]-values[low])*(i-low)


def main():
    runs=[]
    for path in sorted((ROOT/'npu_deploy/artifacts/fpga_fold').glob('*/observer.jsonl')):
        rows=[];errors=0
        for line in path.read_text().splitlines():
            try:row=json.loads(line)
            except ValueError:continue
            if row.get('event')=='task_reply':rows.append(row)
            if row.get('event')=='observer_error':errors+=1
        if not rows:continue
        rtt=[row['host_round_trip_us']/1000 for row in rows]
        runs.append(dict(path=str(path.relative_to(ROOT.parent)),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                         replies=len(rows),rtt_ms={str(p):percentile(rtt,p) for p in (50,95,99,100)},
                         over_10ms=sum(t>10 for t in rtt),over_30hz_period=sum(t>1000/30 for t in rtt),
                         masks=dict(collections.Counter(str(row['violations']) for row in rows)),
                         reference_mismatches=sum(row.get('reference_match') is not True for row in rows),errors=errors))
    guard=json.loads((RUNTIME/'config/guard_profile.json').read_text())
    visual=json.loads((RUNTIME/'config/fold_verify_profile.json').read_text())
    active=json.loads((ROOT/'npu_deploy/fpga_board/active_profile.json').read_text())
    report=dict(evidence_scope='historical local observer logs; not a new board or robot measurement',runs=runs,
                total_replies=sum(r['replies'] for r in runs),
                all_motion_masks_zero=all(set(r['masks'])=={'0'} for r in runs),
                source_audit=dict(policy_observation_order='wrapper.send_action precedes fpga_fold_observer.submit',
                                  motion_feedback_age_limit_us=guard['max_feedback_age_us'],
                                  pl_age_check_enabled='AGE_CHECK_ENABLED = 32\'d0' not in (RUNTIME/'rtl/guard_config.svh').read_text(),
                                  field_ready=active['field_ready'],quality_profile=active['quality_profile'],
                                  visual_calibration=visual['calibration_status'],real_image_extraction_enabled=visual['feature_algorithm']['real_image_extraction_enabled']),
                conclusion='Current FPGA replies do not control dispatch or establish cloth success. 10ms synchronous authorization is incompatible with a substantial fraction of these historical replies; full-cycle timing also includes policy inference.',
                live_board_deployed=False,physical_stop_verified=False,robot_commands_sent=False)
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'current_path_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(runs=len(runs),replies=report['total_replies'],all_motion_masks_zero=report['all_motion_masks_zero'],report=str(OUT/'current_path_audit.json')),ensure_ascii=False))


if __name__=='__main__':main()
