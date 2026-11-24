"""Generate matching profile constants and RTL test vectors from the golden model."""
from dataclasses import replace
import json
from pathlib import Path
import random

from .protocol import CHECK_CORRECTION, RUNNING, Sample
from .reference import evaluate, load_profile, profile_crc
from .replay import samples

ROOT = Path(__file__).resolve().parents[1]


def generate():
    profile = load_profile()
    text = ['// Generated from config/guard_profile.json; signed micro-units.']
    fields = dict(PROFILE_CRC=profile_crc(profile), HANDOFF_STEP=profile['handoff_step'],
        MAX_ACTIONS=profile['max_actions'], JOINT_STEP_LIMIT=profile['joint_step_limit'],
        TRACKING_LIMIT=profile['tracking_limit'], CORRECTION_LIMIT=profile['correction_limit'],
        CORRECTION_STEP_LIMIT=profile['correction_step_limit'],
        START_JOINT_TOLERANCE=profile['start_joint_tolerance'],
        START_GRIPPER_TOLERANCE=profile['start_gripper_tolerance'],
        AGE_CHECK_ENABLED=int(profile['max_feedback_age_us'] is not None),
        MAX_FEEDBACK_AGE_US=profile['max_feedback_age_us'] or 0)
    for name, value in fields.items():
        text.append(f'localparam logic [31:0] {name} = 32\'d{value};')
    for function, key in (('bound_lower','lower'),('bound_upper','upper'),('start_pose','start')):
        text += [f'function automatic logic signed [31:0] {function}(input integer idx);', '  case(idx)']
        text += [f'    {i}: {function} = {value};' for i,value in enumerate(profile[key])]
        text += [f'    default: {function} = 0;', '  endcase', 'endfunction']
    (ROOT/'rtl/guard_config.svh').write_text('\n'.join(text)+'\n')
    all_samples = []
    for path in sorted((ROOT/'examples').glob('real_trace*.json')):
        all_samples.extend(sample for _,sample in samples(json.loads(path.read_text()),profile))
    start=tuple(profile['start'])
    base=Sample(profile_crc(profile),0,RUNNING,0,0xFFFFFFFF,0xFFFFFFFF,start,start,start,start,(0,)*14,(0,)*14)
    all_samples.extend(replace(base,step=step,queue_depth=0) for step in (0,299,300,749,750))
    rng=random.Random(42)
    for number in range(256):
        key=rng.choice(('target','measured','previous','act','correction','previous_correction'))
        vector=list(getattr(base,key)); index=rng.randrange(14)
        vector[index]=rng.choice((-2147483648,2147483647,-2140002,-1610002,-50002,0,20011,150001,2540002))
        all_samples.append(replace(base,flags=15 if number%2 else RUNNING,
                                  **{key:tuple(vector)}))
    rows=[str(len(all_samples))]
    for sample in all_samples:
        result=evaluate(sample,profile)
        rows.append(f'{sample.profile_crc:08x} {sample.step} {sample.flags} '
            f'{sample.feedback_age_us:08x} {sample.queue_depth:08x} '
            f'{result["violations"]:08x} {result["events"]:08x} {result["phase"]}')
        for i in range(14):
            rows.append(' '.join(str(getattr(sample,key)[i]) for key in
                ('target','measured','previous','act','correction','previous_correction')))
    (ROOT/'rtl/vectors.txt').write_text('\n'.join(rows)+'\n')
    print(json.dumps(dict(profile_crc=f'{profile_crc(profile):08x}',vectors=len(all_samples)),indent=2))


if __name__ == '__main__':
    generate()
