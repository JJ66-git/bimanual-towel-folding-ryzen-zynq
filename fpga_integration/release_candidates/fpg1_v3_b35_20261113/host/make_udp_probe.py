"""Create one deterministic out-of-range checker request; no motion command."""
import argparse
import json
from pathlib import Path

from .protocol import RUNNING, UNKNOWN, Sample, frame
from .reference import evaluate, load_profile, profile_crc


def make_probe(profile):
    start = tuple(profile['start'])
    target = list(start)
    target[0] = profile['upper'][0] + 2
    target = tuple(target)
    return Sample(profile_crc(profile), 0, RUNNING, 0, UNKNOWN, UNKNOWN,
                  target, target, target, start, (0,) * 14, (0,) * 14)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('udp_probe_out_of_range.bin'))
    args = parser.parse_args()

    profile = load_profile()
    sample = make_probe(profile)
    result = evaluate(sample, profile)
    if result['violations'] != 1:
        raise RuntimeError('Probe must trigger only target_bounds: {}'.format(result))
    request = frame(1, 1, 0, 0, sample.pack())
    args.out.write_bytes(request)
    print(json.dumps(dict(
        request=str(args.out), bytes=len(request),
        expected_violations='{:08x}'.format(result['violations']),
        expected_events='{:08x}'.format(result['events']),
        expected_phase=result['phase'], motion_commands=False,
    ), indent=2))


if __name__ == '__main__':
    main()
