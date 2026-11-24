#!/usr/bin/env python3
"""Exercise the grasp handover with a simplified MuJoCo cloth and two grips."""

import argparse
from itertools import product
import json
from pathlib import Path

import mujoco
import numpy as np


def scene(jaw_gain):
    return mujoco.MjModel.from_xml_string(f"""
<mujoco model="tabletop_handover">
  <option timestep=".002" gravity="0 0 -9.81"/>
  <worldbody>
    <geom name="table" type="plane" size=".6 .4 .05" friction=".7 .01 .001"/>
    <body name="left_support" pos="-.15 0 .006" mocap="true"/>
    <body name="right_support" pos=".15 0 .006" mocap="true"/>
    <body name="jaw_width_proxy" pos="0 -.25 .15">
      <joint name="jaw_width" type="slide" axis="0 1 0" limited="true" range="0 .08" damping=".20"/>
      <geom type="box" size=".01 .005 .005" mass=".025" contype="0" conaffinity="0"/>
    </body>
    <flexcomp name="towel" type="grid" dim="2" count="13 7 1" spacing=".025 .025 .025"
              pos="0 0 .006" radius=".0025" mass=".075">
      <contact selfcollide="none"/>
      <edge equality="true" solref=".014 1"/>
    </flexcomp>
  </worldbody>
  <equality>
    <connect name="left_grasp" body1="left_support" body2="towel_3" anchor="0 0 0"/>
    <connect name="right_grasp" body1="right_support" body2="towel_87" anchor="0 0 0"/>
  </equality>
  <actuator>
    <position joint="jaw_width" kp="{jaw_gain}" ctrllimited="true" ctrlrange="0 .08"/>
  </actuator>
</mujoco>
""")


def scenario(*, feedback_gate, jaw_gain, right_has_cloth, opening, release_at=None):
    model = scene(jaw_gain)
    data = mujoco.MjData(model)
    left = model.equality("left_grasp").id
    right = model.equality("right_grasp").id
    right_mocap = model.body("right_support").mocapid[0]
    jaw = model.joint("jaw_width").qposadr[0]
    data.eq_active[right] = right_has_cloth
    for _ in range(150):
        mujoco.mj_step(model, data)
    start = data.time
    origin = data.mocap_pos[right_mocap].copy()
    measured_open_at = moved_at = None
    peak_while_both_latched = 0.0
    dual_motion_ticks = 0
    if release_at is None:
        release_at = .03 if feedback_gate else .72
    for _ in range(650):
        now = data.time - start
        width = data.qpos[jaw]
        data.ctrl[0] = opening if now >= release_at else 0.
        if width >= .020:
            data.eq_active[left] = False
        if width >= .025 and measured_open_at is None:
            measured_open_at = now
        may_move = ((right_has_cloth and measured_open_at is not None) if feedback_gate else now >= .22)
        if may_move and moved_at is None:
            moved_at = now
        if moved_at is not None:
            fraction = min(1.0, (now - moved_at) / .36)
            data.mocap_pos[right_mocap] = origin + fraction * np.array((.045, 0., .055))
        mujoco.mj_step(model, data)
        if (moved_at is not None and now - moved_at < .36
                and data.eq_active[left] and data.eq_active[right]):
            dual_motion_ticks += 1
            rows = (data.efc_type[:data.nefc] == mujoco.mjtConstraint.mjCNSTR_EQUALITY)
            rows &= data.efc_id[:data.nefc] == left
            force = float(np.linalg.norm(data.efc_force[:data.nefc][rows]))
            peak_while_both_latched = max(peak_while_both_latched, force)
    return dict(measured_open_at_s=measured_open_at, right_started_at_s=moved_at,
                moving_with_both_grasps_s=dual_motion_ticks * model.opt.timestep,
                peak_left_constraint_force_N=peak_while_both_latched,
                final_left_opening_mm=float(1000 * data.qpos[jaw]),
                right_edge_lift_mm=float(1000 * (data.flexvert_xpos[87, 2] - origin[2])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sweep", action="store_true",
                        help="Also test opening delays, slow jaws and absent right grasp")
    args = parser.parse_args()
    modes = {
        "scheduled_motion_late_small_opening": dict(feedback_gate=False, jaw_gain=12., right_has_cloth=True, opening=.013),
        "measured_release_then_motion": dict(feedback_gate=True, jaw_gain=12., right_has_cloth=True, opening=.035),
        "slow_left_gripper": dict(feedback_gate=True, jaw_gain=1.5, right_has_cloth=True, opening=.035),
        "left_opening_insufficient": dict(feedback_gate=True, jaw_gain=12., right_has_cloth=True, opening=.013),
        "right_owns_no_cloth": dict(feedback_gate=True, jaw_gain=12., right_has_cloth=False, opening=.035),
    }
    results = {name: scenario(**settings) for name, settings in modes.items()}
    assert results["scheduled_motion_late_small_opening"]["moving_with_both_grasps_s"] > .1
    for name in ("measured_release_then_motion", "slow_left_gripper"):
        case = results[name]
        assert case["right_started_at_s"] >= case["measured_open_at_s"]
        assert case["moving_with_both_grasps_s"] == 0.0
    for name in ("left_opening_insufficient", "right_owns_no_cloth"):
        assert results[name]["right_started_at_s"] is None
    sweep = []
    if args.sweep:
        for gain, opening, delay, right_has_cloth in product(
                (1.5, 5., 12.), (.013, .035), (.03, .24, .52), (False, True)):
            conditions = dict(jaw_gain=gain, opening=opening, release_at=delay,
                              right_has_cloth=right_has_cloth)
            scheduled = scenario(feedback_gate=False, **conditions)
            measured = scenario(feedback_gate=True, **conditions)
            assert measured["moving_with_both_grasps_s"] == 0.
            if right_has_cloth and opening >= .025:
                assert measured["right_started_at_s"] >= measured["measured_open_at_s"]
            else:
                assert measured["right_started_at_s"] is None
            sweep.append(dict(conditions=conditions, scheduled=scheduled, measured=measured))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(dict(
        mujoco_version=mujoco.__version__,
        scope="Deformable cloth and idealized grasp constraints. No Piper linkage, camera calibration, or ACT inference.",
        cases=results, sweep=sweep), indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
