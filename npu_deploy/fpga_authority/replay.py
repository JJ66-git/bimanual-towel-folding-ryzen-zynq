#!/usr/bin/env python3
"""Replay recorded candidates through the integer reference and unchanged RTL.

Missing feedback ages remain UNKNOWN. This is a numeric compatibility audit,
never a retrospective freshness permit or a physical task-success classifier.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys

PROJECT=Path(__file__).resolve().parents[2]
WORKSPACE=PROJECT/'fpga_integration/fpga_code/fpga_workspace'
sys.path.insert(0,str(WORKSPACE))
from host.protocol import Sample,quantize,RUNNING,CHECK_CORRECTION,UNKNOWN
from host.reference import load_profile,profile_crc,evaluate,ARM


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def samples_from_trace(trace,profile):
    prior=(0.,)*14;rows=trace['rows'];start=rows[0]['tick_monotonic_s']
    for row in rows:
        correction=row['correction']
        flags=RUNNING|(CHECK_CORRECTION if max(abs(correction[i]) for i in ARM)>1e-4 else 0)
        yield row,Sample(profile_crc(profile),row['step'],flags,
            max(0,int((row['tick_monotonic_s']-start)*1e6)),UNKNOWN,UNKNOWN,
            *(quantize(v) for v in (row['target'],row['measured'],row['previous'],row['act_action'],correction,prior)))
        prior=correction


def write_vectors(path,samples,profile):
    with Path(path).open('w') as out:
        out.write(str(len(samples))+'\n')
        for sample in samples:
            result=evaluate(sample,profile)
            out.write('%08x %d %d %08x %08x %08x %08x %d\n'%(sample.profile_crc,sample.step,sample.flags,
                sample.feedback_age_us,sample.queue_depth,result['violations'],result['events'],result['phase']))
            vectors=(sample.target,sample.measured,sample.previous,sample.act,sample.correction,sample.previous_correction)
            for i in range(14):out.write(' '.join(str(v[i]) for v in vectors)+'\n')


def run_rtl(samples,profile,out,tools):
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True);write_vectors(out/'vectors.txt',samples,profile)
    lib=next((Path(tools)/'usr/lib').glob('*/ivl'));rtl=WORKSPACE/'rtl';executable=out/'replay.vvp'
    command=[str(Path(tools)/'usr/bin/iverilog'),'-B'+str(lib),'-g2012','-I'+str(rtl),'-s','tb_motion_guard',
        '-o',str(executable),str(rtl/'motion_guard.sv'),str(rtl/'tb_motion_guard.sv')]
    compiled=subprocess.run(command,capture_output=True,text=True,timeout=60)
    (out/'compile.log').write_text(compiled.stdout+compiled.stderr)
    if compiled.returncode:raise RuntimeError('Recorded-candidate RTL compilation failed; see '+str(out/'compile.log'))
    result=subprocess.run([str(Path(tools)/'usr/bin/vvp'),'-M'+str(lib),str(executable)],cwd=out,capture_output=True,text=True,timeout=180)
    (out/'run.log').write_text(result.stdout+result.stderr)
    return dict(passed=result.returncode==0 and 'PASS '+str(len(samples))+' motion guard vectors' in result.stdout,
        vectors=len(samples),log=str(out/'run.log'),rtl_sha256=digest(rtl/'motion_guard.sv'),output=result.stdout.strip())


def audit_trace(path,label,outcome,profile):
    data=json.loads(Path(path).read_text());pairs=list(samples_from_trace(data,profile));violations=Counter();bad=[]
    for row,sample in pairs:
        result=evaluate(sample,profile)
        if result['violations']:
            violations.update(result['names']);bad.append(dict(step=sample.step,mask=result['violations'],reasons=result['names']))
    grippers={}
    for side,index in (('left',6),('right',13)):
        rows=data['rows'];target=[r['target'][index]*1000 for r in rows];measured=[r['measured'][index]*1000 for r in rows]
        errors=sorted(abs(r['measured'][index]-r['previous'][index])*1000 for r in rows)
        opened=next((r['step'] for r in rows if r['target'][index]>.04),None)
        grippers[side]=dict(target_identical_to_act=all(s.target[index]==s.act[index] and s.correction[index]==0 for _,s in pairs),
            units='meters in action vectors; millimeters in this summary',target_range_mm=[min(target),max(target)],
            feedback_range_mm=[min(measured),max(measured)],open_over_40mm_step=opened,
            close_under_10mm_step=next((r['step'] for r in rows if opened is not None and r['step']>opened and r['target'][index]<.01),None),
            error_to_previous_target_mm_median=statistics.median(errors),error_to_previous_target_mm_p95=errors[int((len(errors)-1)*.95)],
            synchronized_per_command_tracking_error=False)
    summary=dict(label=label,trace=str(path),trace_sha256=digest(path),outcome_label=outcome,outcome_label_source='existing operator evidence',
        samples=len(pairs),numeric_clear=len(pairs)-len(bad),numeric_rejected=len(bad),violation_counts=dict(violations),first_rejections=bad[:15],
        missing_feedback_age=len(pairs),strict_authority_eligible_samples=0,missing_queue_depth=len(pairs),grippers=grippers)
    return summary,[s for _,s in pairs]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source-workspace',type=Path,required=True)
    p.add_argument('--comparison',type=Path);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--rtl-tools',type=Path,default=Path('/home/ysyx/FPGA_competition/downloads/rtl_tools/runtime'))
    p.add_argument('--skip-rtl',action='store_true');args=p.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    comparison=args.comparison or args.source_workspace/'local_environment_record/task_failure_review_20261114/success_failure_comparison.json'
    sources=json.loads(comparison.read_text())['runs'];profile=load_profile();runs=[];samples=[]
    for row in sources:
        item,values=audit_trace(args.source_workspace/row['trace'],row['label'],row['task_outcome'],profile)
        runs.append(item);samples.extend(values)
    report=dict(schema='fpga_recorded_candidates_v1',runs=runs,samples=len(samples),guard_profile_crc=profile_crc(profile),
        numeric_contract_preserved=all(g['target_identical_to_act'] for run in runs for g in run['grippers'].values()),
        eligible_for_policy_execution=False,real_pl=False,robot_commands_sent=False,
        limitation='Unknown historic feedback ages and queue depths are not synthesized. Motion constraints cannot certify cloth-task outcome.')
    if not args.skip_rtl:report['rtl']=run_rtl(samples,profile,args.out/'rtl_replay',args.rtl_tools)
    report['passed']=report['numeric_contract_preserved'] and report.get('rtl',{}).get('passed',args.skip_rtl)
    (args.out/'replay.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('RECORDED_REPLAY='+json.dumps(dict(passed=report['passed'],runs=len(runs),samples=len(samples),report=str(args.out/'replay.json'))))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
