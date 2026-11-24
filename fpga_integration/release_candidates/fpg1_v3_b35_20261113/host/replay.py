"""Export recorded requests and expected verdicts without opening any device."""
import argparse
from dataclasses import replace
import json
from pathlib import Path

from .protocol import CHECK_CORRECTION, RUNNING, UNKNOWN, Sample, frame, quantize
from .reference import evaluate, load_profile, profile_crc


def samples(trace, profile):
    rows = trace['rows']
    if not rows:
        return
    initial = float(rows[0]['tick_monotonic_s'])
    previous_correction = (0,)*14
    for sequence, row in enumerate(rows):
        correction = quantize(row['correction'])
        flags = RUNNING | (CHECK_CORRECTION if max(abs(v) for v in row['correction']) > 1e-4 else 0)
        # Existing traces lack coherent per-joint sample ages and per-tick queue depth.
        sample = Sample(profile_crc(profile), int(row['step']), flags,
            round((float(row['tick_monotonic_s'])-initial)*1_000_000), UNKNOWN, UNKNOWN,
            quantize(row['target']), quantize(row['measured']), quantize(row['previous']),
            quantize(row['act_action']), correction, previous_correction)
        yield sequence, sample
        previous_correction = correction


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', type=Path, default=Path('examples/real_trace_excerpt.json'))
    parser.add_argument('--out', type=Path, default=Path('output/replay'))
    parser.add_argument('--profile', type=Path)
    args = parser.parse_args()
    profile = load_profile(args.profile)
    trace = json.loads(args.trace.read_text())
    # A new directory avoids mixing two exports and a partial failed export.
    args.out.mkdir(parents=True, exist_ok=False)
    total = violations = 0
    with (args.out/'requests.bin').open('wb') as binary, (args.out/'expected.jsonl').open('w') as rows:
        for sequence, sample in samples(trace, profile):
            binary.write(frame(1, 1, sequence, sample.elapsed_us, sample.pack()))
            result = evaluate(sample, profile)
            rows.write(json.dumps(dict(sequence=sequence, step=sample.step, **result))+'\n')
            total += 1
            violations += bool(result['violations'])
    report = dict(rows=total, rows_with_flags=violations, hardware_access=False,
                  motion_commands=False, purpose='Integer reference flags only, not task success evaluation',
                  profile_crc=profile_crc(profile), feedback_age='unknown', queue_depth='unknown',
                  step_budget_note='Default profile budget 750; replaying a 900-step run requires its actual profile')
    (args.out/'summary.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
