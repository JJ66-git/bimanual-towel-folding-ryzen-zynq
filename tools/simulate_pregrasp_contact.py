#!/usr/bin/env python3
"""Measure cloth disturbance from a low jaw sweep in the simplified MuJoCo scene."""

import argparse
from itertools import product
import json
from pathlib import Path

import mujoco
import numpy as np

from simulate_towel_robustness import TIMESTEP, advance, initial_towel, scene, vertices


def run(friction, low_height, shift, raised):
    model = scene(friction, .075, contact_probe=True)
    data = mujoco.MjData(model)
    probe = model.body("approach_probe").mocapid[0]
    goal = initial_towel()[0, 0, :2] + np.array((.004, .005))
    start = goal - np.array((shift, 0.))
    height = low_height + (.010 if raised else 0.)
    data.mocap_pos[probe] = [*start, height]
    advance(model, data, .25)
    initial = vertices(data).copy()

    for t in np.linspace(0, 1, round(.25 / TIMESTEP) + 1)[1:]:
        fraction = t**3 * (10 + t * (-15 + 6*t))
        data.mocap_pos[probe, :2] = start + fraction * (goal - start)
        mujoco.mj_step(model, data)
    for t in np.linspace(0, 1, round(.20 / TIMESTEP) + 1)[1:]:
        data.mocap_pos[probe, 2] = low_height + (height - low_height) * (1 - t)
        mujoco.mj_step(model, data)

    movement = np.linalg.norm(vertices(data)[:, :, :2] - initial[:, :, :2], axis=-1) * 1000
    return dict(corner_drift_mm=float(movement[0, 0]),
                cloth_drift_p90_mm=float(np.percentile(movement, 90)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    cases = [
        dict(friction=friction, jaw_height_mm=height * 1000, travel_mm=shift * 1000,
             low=run(friction, height, shift, False),
             above_then_lower=run(friction, height, shift, True))
        for friction, height, shift in product((.50, .80, 1.10), (.0065, .008, .010), (.010, .020))
    ]
    p90 = {
        mode: {metric: float(np.percentile([row[mode][metric] for row in cases], 90))
               for metric in cases[0][mode]}
        for mode in ("low", "above_then_lower")
    }
    assert p90["above_then_lower"]["corner_drift_mm"] < p90["low"]["corner_drift_mm"]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(dict(
        mujoco_version=mujoco.__version__, robot_commands_sent=False,
        scope="One MuJoCo jaw proxy pushes settled flex cloth. Proxy clearance has no Piper table calibration.",
        p90=p90, cases=cases), indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
