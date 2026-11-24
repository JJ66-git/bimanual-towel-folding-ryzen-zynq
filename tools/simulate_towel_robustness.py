#!/usr/bin/env python3
"""Compare fixed and observed edge targets for two folds of a MuJoCo flex towel."""

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from functools import partial
from itertools import product
import json
from pathlib import Path

import cv2
import mujoco
import numpy as np


NX, NY = 13, 9
SPACING = .025
TIMESTEP = .0025
PIXELS_PER_M = 1200
IMAGE_ORIGIN = np.array((360, 280))
IMAGE_SHAPE = (560, 720)
CAPTURE_XY_M = .011
CAPTURE_Z_M = .017
FIRST_EDGE = ((0, 0), (NX - 1, 0))
FAR_EDGE = ((0, NY - 1), (NX - 1, NY - 1))
SECOND_EDGE = ((NX - 1, 0), (NX - 1, NY - 1), (NX - 1, NY // 2))
GRASP_POINTS = tuple(dict.fromkeys((*FIRST_EDGE, *SECOND_EDGE)))
LOCAL_RETRY_M = .009


def initial_towel():
    x, y = np.meshgrid((np.arange(NX) - NX // 2) * SPACING,
                       (np.arange(NY) - NY // 2) * SPACING, indexing="ij")
    return np.stack((x, y, np.full_like(x, .007)), axis=-1)


def scene(friction, mass, *, contact_probe=False, augment=None):
    base = initial_towel()
    movables, constraints = [], []
    for xy in GRASP_POINTS:
        x, y = xy
        name = f"grip_{x}_{y}"
        xyz = " ".join(map(str, base[xy]))
        movables.append(f'<body name="{name}" pos="{xyz}" mocap="true"/>')
        constraints.append(f'<connect name="{name}" body1="{name}" body2="towel_{x*NY+y}" '
                           'anchor="0 0 0" active="false" solref=".012 1"/>')
    if contact_probe:
        movables.append('<body name="approach_probe" pos="0 0 .04" mocap="true">'
                        '<geom type="box" size=".009 .007 .006" friction=".9 .01 .001"/></body>')
    xml = f"""
<mujoco model="FlexiFold tabletop towel">
  <option timestep="{TIMESTEP}" gravity="0 0 -9.81" integrator="implicitfast" tolerance="1e-7" iterations="45"/>
  <worldbody>
    <light name="overhead" pos="0 0 .85" dir="0 0 -1"/>
    <geom name="table" type="plane" size=".6 .4 .03" rgba=".66 .68 .70 1" friction="{friction} .01 .001"/>
    {''.join(movables)}
    <flexcomp name="towel" type="grid" dim="2" count="{NX} {NY} 1" spacing="{SPACING} {SPACING} {SPACING}"
              pos="0 0 .007" radius=".002" mass="{mass}" rgba=".09 .38 .64 1">
      <contact selfcollide="auto" friction="{friction} .01 .001" solref=".012 1"/>
      <edge equality="true" solref=".020 1" damping=".15"/>
    </flexcomp>
  </worldbody>
  <equality>{''.join(constraints)}</equality>
</mujoco>
"""
    return mujoco.MjModel.from_xml_string(augment(xml) if augment is not None else xml)


def vertices(data):
    return data.flexvert_xpos.reshape(NX, NY, 3)


def advance(model, data, seconds):
    for _ in range(round(seconds / TIMESTEP)):
        mujoco.mj_step(model, data)


def rotate(angle_deg):
    angle = np.deg2rad(angle_deg)
    sine, cosine = np.sin(angle), np.cos(angle)
    return np.array(((cosine, -sine), (sine, cosine)))


def transform_towel(model, data, offset_m, yaw_deg):
    """Perturb the cloth pose while keeping its material geometry."""
    current = vertices(data).copy()
    center = current[:, :, :2].mean(axis=(0, 1))
    shifted = (current[:, :, :2] - center) @ rotate(yaw_deg).T + center + offset_m
    changes = (shifted - current[:, :, :2]).reshape(-1, 2)
    for i, change in enumerate(changes):
        body = model.body(f"towel_{i}")
        start = model.jnt_qposadr[body.jntadr[0]]
        data.qpos[start:start + 2] += change
    data.qvel[:] = 0.
    mujoco.mj_forward(model, data)


def outline_observation(model, data, rng, position_error_mm):
    """Observe a rasterized top view of the deformed surface with pixel noise."""
    projected = vertices(data).reshape(-1, 3)[:, :2] * PIXELS_PER_M + IMAGE_ORIGIN
    projected += rng.normal(0, .65, size=projected.shape)
    triangles = model.flex_elem.reshape(-1, 3)
    mask = np.zeros(IMAGE_SHAPE, np.uint8)
    for triangle in triangles:
        cv2.fillConvexPoly(mask, np.rint(projected[triangle]).astype(np.int32), 255)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rect = cv2.minAreaRect(max(contours, key=cv2.contourArea))
    corners = (cv2.boxPoints(rect) - IMAGE_ORIGIN) / PIXELS_PER_M
    corners += rng.normal(0, position_error_mm / 1000., size=(1, 2))
    near, far = np.split(corners[np.argsort(corners[:, 1])], 2)
    return np.stack((near[np.argsort(near[:, 0])], far[np.argsort(far[:, 0])]))


def raised_surface_approach(model, data, reference, rng, position_error_mm):
    """Approximate an overhead depth threshold and inspect the far corner locally."""
    height = .0045
    surfaces = vertices(data).reshape(-1, 3)
    mask = np.zeros(IMAGE_SHAPE, np.uint8)
    for ids in model.flex_elem.reshape(-1, 3):
        triangle = surfaces[ids]
        polygon = []
        for start, end in zip(triangle, np.roll(triangle, -1, axis=0), strict=True):
            if start[2] > height:
                polygon.append(start[:2])
            if (start[2] > height) != (end[2] > height):
                fraction = (height - start[2]) / (end[2] - start[2])
                polygon.append(start[:2] + fraction * (end[:2] - start[:2]))
        if len(polygon) >= 3:
            pixels = np.asarray(polygon) * PIXELS_PER_M + IMAGE_ORIGIN
            cv2.fillConvexPoly(mask, np.rint(pixels).astype(np.int32), 255)
    positions = cv2.findNonZero(mask)
    if positions is None:
        return None
    surface_xy = (positions[:, 0, :] - IMAGE_ORIGIN) / PIXELS_PER_M
    error = np.linalg.norm(surface_xy - reference, axis=1)
    closest = int(error.argmin())
    if not .016 < error[closest] < .055:
        return None
    return surface_xy[closest] + rng.normal(0, .0005 * position_error_mm, size=2)


def grip_id(model, xy):
    name = f"grip_{xy[0]}_{xy[1]}"
    return model.body(name).mocapid[0], model.equality(name).id


def capture(model, data, points, targets_xy, *, heights, retry_offsets=(), groups=None):
    """Use cloth vertices to evaluate capture and count successful retry targets."""
    grabbed = []
    retries_used = 0
    positions = np.array([vertices(data)[xy] for xy in points])
    in_height = np.abs(positions[:, 2] - heights) <= CAPTURE_Z_M
    for group in groups or tuple((i,) for i in range(len(points))):
        best_fit = []
        best_attempt = 0
        # Two layers in one jaw must match the same candidate position.
        for attempt, offset in enumerate(((0., 0.), *retry_offsets)):
            fit = [i for i in group if in_height[i]
                   and np.linalg.norm(targets_xy[i] + offset - positions[i, :2]) <= CAPTURE_XY_M]
            if len(fit) > len(best_fit):
                best_fit, best_attempt = fit, attempt
            if len(fit) == len(group):
                break
        for i in best_fit:
            grip, equality = grip_id(model, points[i])
            data.mocap_pos[grip] = positions[i]
            data.eq_active[equality] = True
            grabbed.append(i)
        retries_used += len(best_fit) if best_attempt else 0
    return grabbed, retries_used


def sweep_fold(model, data, grasped, goal, *, height, eased, seconds=.95, observe=None):
    grips, constraints = zip(*(grip_id(model, xy) for xy in grasped), strict=True)
    grips = list(grips)
    start = data.mocap_pos[grips].copy()
    # Record stationary launch and arrival for the finite differences.
    path = [start.copy()]
    for t in np.linspace(0., 1., round(seconds / TIMESTEP) + 1)[1:]:
        distance = t**3 * (10 + t * (-15 + 6*t)) if eased else t
        target = start + (goal - start) * distance
        target[:, 2] += height * np.sin(np.pi * distance)
        data.mocap_pos[grips] = target
        path.append(target.copy())
        mujoco.mj_step(model, data)
        if observe is not None:
            observe(target, True)
    path.append(path[-1])
    advance(model, data, .10)
    data.eq_active[list(constraints)] = False
    for _ in range(round(.40 / TIMESTEP)):
        mujoco.mj_step(model, data)
        if observe is not None:
            observe(path[-1], False)
    acceleration = np.linalg.norm(np.diff(path, n=2, axis=0), axis=-1) / TIMESTEP**2
    return float(acceleration.max())


def alignment(data):
    cloth = vertices(data)
    # Pair known material coordinates only for grading. The controller uses the outline.
    across_y = cloth[:, :NY//2, :2] - cloth[:, :NY//2:-1, :2]
    across_x = cloth[:NX//2, :, :2] - cloth[:NX//2:-1, :, :2]
    return [float(np.percentile(np.linalg.norm(axis, axis=-1), 90) * 1000)
            for axis in (across_y, across_x)]


@dataclass(frozen=True)
class Case:
    name: str
    offset_x_mm: float = 0.
    offset_y_mm: float = 0.
    yaw_deg: float = 0.
    lay_skew_mm: float = 0.
    second_shift_mm: float = 0.
    second_yaw_deg: float = 0.
    friction: float = .9
    mass_kg: float = .075
    rgb_error_mm: float = 1.


MODES = {
    "fixed": (False, False, False, False),
    "first_retarget": (True, False, False, False),
    "both_retarget": (True, True, False, False),
    "both_retarget_retry": (True, True, True, False),
    "both_retarget_eased": (True, True, False, True),
    "both_retarget_retry_eased": (True, True, True, True),
    "both_retarget_slow_first_lay": (True, True, True, True),
    "both_retarget_relay_eased": (True, True, True, True),
    "both_retarget_low_second_lay": (True, True, True, True),
}


class FoldCycle:
    """Simulated approach and retreat for the two visible kinematic grippers."""

    HOME = np.array(((-.215, -.19, .155), (.215, -.19, .155)))

    def __init__(self, model, data, on_frame):
        self.model, self.data = model, data
        self.on_frame = on_frame
        self.tools = self.HOME.copy()
        self.stages = []
        self.stage = None

    def show(self, stage, closed=(False, False)):
        if stage != self.stage:
            self.stages.append(dict(stage=stage, time_s=round(self.data.time, 4)))
            self.stage = stage
        if self.on_frame is not None:
            self.on_frame(self.model, self.data, stage, self.tools, closed)

    def move(self, goal, seconds, stage):
        start = self.tools.copy()
        for t in np.linspace(0., 1., round(seconds / TIMESTEP) + 1)[1:]:
            smooth = t**3 * (10 + t * (-15 + 6*t))
            self.tools[:] = start + (goal - start) * smooth
            mujoco.mj_step(self.model, self.data)
            self.show(stage)

    def approach(self, targets, stage, *, second=False):
        goal = self.tools.copy()
        if second:
            # The second fold only regrips with the right tool. The cloth
            # retains its additional idealized support along that same edge.
            goal[0] = self.HOME[0]
            goal[1] = (*targets[0], .008)
        else:
            goal = np.column_stack((targets, [.004, .004]))
        high = goal.copy()
        high[:, 2] = np.maximum(high[:, 2], .105)
        self.move(high, .43, stage)
        self.move(goal, .28, stage)

    def sweep_observer(self, target, closed):
        self.tools[:] = target
        self.show("First sweep" if closed else "First release", (closed, closed))

    def second_observer(self, target, closed):
        self.tools[1] = target[:2].mean(axis=0)
        self.show("Second sweep" if closed else "Second release", (False, closed))

    def return_home(self):
        lifted = self.tools.copy()
        lifted[:, 2] = self.HOME[:, 2]
        self.move(lifted, .38, "Retreat")
        self.move(self.HOME, .61, "Return home")
        released = not any(self.data.eq_active[grip_id(self.model, point)[1]]
                           for point in GRASP_POINTS)
        return bool(released and np.allclose(self.tools, self.HOME, rtol=0, atol=1e-8))


def trial(job, *, full_cycle=False, on_frame=None, model_builder=scene):
    case, seed, mode = job
    first_adapt, second_adapt, retry, eased = MODES[mode]
    rng = np.random.default_rng(seed)
    model = model_builder(case.friction, case.mass_kg)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    transform_towel(model, data, np.array([case.offset_x_mm, case.offset_y_mm]) / 1000., case.yaw_deg)
    advance(model, data, .2)
    cycle = FoldCycle(model, data, on_frame) if full_cycle else None
    flat = initial_towel()
    nominal_near = np.array([flat[xy][:2] for xy in FIRST_EDGE])
    nominal_far = np.array([flat[xy][:2] for xy in FAR_EDGE])
    first_targets, first_goals = outline_observation(model, data, rng, case.rgb_error_mm)
    if first_adapt:
        # Move a few mm inward along the edge normal to keep cloth in the jaws.
        first_targets += .003 * (first_goals - first_targets) / np.linalg.norm(
            first_goals - first_targets, axis=1, keepdims=True)
    else:
        first_targets, first_goals = nominal_near, nominal_far.copy()
    if cycle is not None:
        cycle.show("Ready")
        cycle.approach(first_targets, "First approach")
    caught, _ = capture(model, data, FIRST_EDGE, first_targets, heights=(.004, .004))
    result = dict(case=asdict(case), mode=mode, first_claws=len(caught), first_fold=False, second_layers=0,
                  second_fold=False, first_p90_mm=None, final_p90_mm=None, peak_accel_m_s2=None,
                  second_captures_after_retry=0, relay_attempted=False, relay_executed=False)
    if cycle is not None:
        result.update(returned_home=False, cycle_complete=False, stages=cycle.stages)
    if len(caught) != len(FIRST_EDGE):
        return result
    if cycle is not None:
        cycle.show("First grasp", (True, True))
    # Keep the first fold intentionally skewed before testing the second grasp.
    first_goals[1, 1] -= case.lay_skew_mm / 1000.
    first_seconds = 1.15 if mode == "both_retarget_slow_first_lay" else .95
    first_observation = {} if cycle is None else {"observe": cycle.sweep_observer}
    first_accel = sweep_fold(model, data, FIRST_EDGE, np.column_stack((first_goals, [.011, .011])),
                             height=.115, eased=eased, seconds=first_seconds, **first_observation)
    first_error, _ = alignment(data)
    result["first_p90_mm"] = first_error
    result["first_fold"] = first_error < 24.
    result["peak_accel_m_s2"] = first_accel
    if not result["first_fold"]:
        return result
    transform_towel(model, data, np.array((case.second_shift_mm, -case.second_shift_mm / 3)) / 1000.,
                    case.second_yaw_deg)
    advance(model, data, .12)

    observed_near, observed_far = outline_observation(model, data, rng, case.rgb_error_mm)
    if second_adapt:
        left_near, right_near = observed_near
        left_far, right_far = observed_far
    else:
        left_near, right_near = nominal_near + np.array((0., .1))
        left_far, right_far = nominal_far
    if mode == "both_retarget_relay_eased":
        raised = raised_surface_approach(model, data, right_far, rng, case.rgb_error_mm)
        if raised is not None:
            result["relay_attempted"] = True
            top_edge = SECOND_EDGE[:1]
            caught, _ = capture(model, data, top_edge, [raised], heights=(.008,))
            if caught:
                result["relay_executed"] = True
                relay_accel = sweep_fold(model, data, top_edge, np.array([[*right_far, .009]]),
                                         height=.04, eased=True, seconds=.65)
                first_accel = max(first_accel, relay_accel)
                (left_near, right_near), (left_far, right_far) = outline_observation(
                    model, data, rng, case.rgb_error_mm)
    second_targets = np.array([right_far, right_far, right_near])
    second_goals = np.array([left_far, left_far, left_near])
    retries = ()
    if retry:
        lateral = right_far - left_far
        lateral = lateral / np.linalg.norm(lateral)
        normal = np.array((-lateral[1], lateral[0]))
        retries = (-lateral * LOCAL_RETRY_M, normal * LOCAL_RETRY_M,
                   -normal * LOCAL_RETRY_M, lateral * LOCAL_RETRY_M)
    if cycle is not None:
        cycle.approach(second_targets, "Second approach", second=True)
    captured, retry_captures = capture(model, data, SECOND_EDGE, second_targets,
                                      heights=(.008, .008, .006), retry_offsets=retries,
                                      groups=((0, 1), (2,)))
    result["second_layers"] = len(captured)
    result["second_captures_after_retry"] = retry_captures
    if len(captured) != len(SECOND_EDGE):
        return result
    if cycle is not None:
        cycle.show("Second grasp", (False, True))
    second_observation = {} if cycle is None else {"observe": cycle.second_observer}
    landing = np.column_stack((second_goals, [.018, .010, .010]))
    if mode == "both_retarget_low_second_lay":
        # Extend along the existing sweep direction and let the held layers
        # reach the cloth lower. The fold arc and release dwell stay the same.
        start_xy = np.array([data.mocap_pos[grip_id(model, point)[0], :2]
                             for point in SECOND_EDGE])
        direction = landing[:, :2] - start_xy
        direction /= np.linalg.norm(direction, axis=1, keepdims=True)
        landing[:, :2] += .003 * direction
        landing[:, 2] -= .003
    second_accel = sweep_fold(model, data, SECOND_EDGE, landing,
                              height=.155, eased=eased, **second_observation)
    final = alignment(data)
    result["final_p90_mm"] = final
    result["second_fold"] = max(final) < 25.
    result["peak_accel_m_s2"] = max(first_accel, second_accel)
    if cycle is not None:
        result["returned_home"] = cycle.return_home()
        result["returned_alignment_p90_mm"] = alignment(data)
        result["cycle_complete"] = bool(result["returned_home"] and result["second_fold"]
                                        and max(result["returned_alignment_p90_mm"]) < 25.)
        cycle.show("Cycle complete" if result["cycle_complete"] else "Cycle incomplete")
    return result


def scenario_set():
    yield Case("reference")
    for x, y in product((-20., -10., 0., 10., 20.), repeat=2):
        if x or y:
            yield Case(f"placement_{x:+.0f}_{y:+.0f}", x, y)
    for skew, shift in product((0., 10., 18.), (-16., 0., 16.)):
        yield Case(f"midfold_{skew:.0f}_{shift:+.0f}", lay_skew_mm=skew, second_shift_mm=shift,
                   second_yaw_deg=5. if shift else -5., rgb_error_mm=2.)
    rng = np.random.default_rng(1119)
    for i in range(16):
        x, y = rng.uniform(-20., 20., size=2)
        yield Case(f"combined_{i:02d}", x, y, rng.uniform(-7., 7.), rng.uniform(0., 18.),
                   rng.uniform(-16., 16.), rng.uniform(-6., 6.), rng.uniform(.45, 1.05),
                   rng.uniform(.060, .120), rng.uniform(1., 3.))


def holdout_set(seed):
    rng = np.random.default_rng(seed)
    for i in range(30):
        x, y = rng.uniform(-20., 20., size=2)
        yield Case(f"holdout_{i:02d}", x, y, rng.uniform(-8., 8.), rng.uniform(0., 22.),
                   rng.uniform(-20., 20.), rng.uniform(-8., 8.), rng.uniform(.40, 1.15),
                   rng.uniform(.055, .130), rng.uniform(1.5, 4.))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--limit", type=int)
    p.add_argument("--holdout-seed", type=int)
    p.add_argument("--full-cycle", action="store_true",
                   help="Include separate gripper approaches, retreat and return")
    p.add_argument("--mode", action="append", choices=MODES,
                   help="Run only the selected mode or modes")
    args = p.parse_args()
    if args.workers < 1 or (args.limit is not None and args.limit < 1):
        p.error("workers and limit must be positive")
    modes = tuple(dict.fromkeys(args.mode or MODES))
    cases = list(holdout_set(args.holdout_seed) if args.holdout_seed is not None else scenario_set())[:args.limit]
    jobs = [(case, 1763 + idx, mode) for idx, case in enumerate(cases) for mode in modes]
    cv2.setNumThreads(1)
    with ProcessPoolExecutor(max_workers=args.workers) as workers:
        results = list(workers.map(partial(trial, full_cycle=args.full_cycle), jobs, chunksize=1))
    summary = {}
    for mode in modes:
        rows = [r for r in results if r["mode"] == mode]
        second_errors = [max(r["final_p90_mm"]) for r in rows if r["second_fold"]]
        summary[mode] = dict(cases=len(rows), first_claws=sum(r["first_claws"]==2 for r in rows),
                             first_fold=sum(r["first_fold"] for r in rows),
                             second_layers=sum(r["second_layers"]==3 for r in rows),
                             second_fold=sum(r["second_fold"] for r in rows),
                             retry_capture_cases=sum(r["second_captures_after_retry"] > 0 for r in rows),
                             relay_attempts=sum(r["relay_attempted"] for r in rows),
                             relay_executed=sum(r["relay_executed"] for r in rows),
                             final_alignment_median_mm=float(np.median(second_errors)) if second_errors else None)
        if args.full_cycle:
            summary[mode]["completed_cycles"] = sum(r["cycle_complete"] for r in rows)
        print(mode, summary[mode], flush=True)
    p95_by_mode = {}
    for mode in modes:
        paired = [r["peak_accel_m_s2"] for r in results if r["mode"] == mode and r["second_fold"]]
        p95_by_mode[mode] = float(np.percentile(paired, 95)) if paired else None
    if len(cases) > 1 and {"fixed", "both_retarget_retry_eased"}.issubset(modes):
        assert summary["both_retarget_retry_eased"]["second_fold"] > summary["fixed"]["second_fold"]
    if args.holdout_seed is None and "both_retarget_retry_eased" in modes:
        reference = {r["mode"]: r for r in results if r["case"]["name"]=="reference"}
        assert reference["both_retarget_retry_eased"]["second_fold"], reference
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(dict(mujoco_version=mujoco.__version__, robot_commands_sent=False,
        scope="Deformable 13x9 cloth with approximate surface friction and top-view contour. "
              "Kinematic grip proxies with a distance/layer capture model and no Piper collision model or ACT. "
              "Retry tests candidate grasp coverage without counting real repositioning time. "
              "The second fold uses paired corner support and idealized layer retention.",
        full_cycle=args.full_cycle,
        scenario_suite="randomized" if args.holdout_seed is not None else "grid_and_randomized",
        holdout_seed=args.holdout_seed,
        summary=summary, successful_runs_peak_acceleration_p95_m_s2=p95_by_mode,
        results=results), indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
