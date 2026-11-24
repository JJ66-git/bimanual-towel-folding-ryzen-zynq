#!/usr/bin/env python3
"""Replay right-arm towel sweeps, then compare bounded feedback recovery in MuJoCo."""

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import sys
import time

import cv2
import mujoco
import numpy as np

import simulate_towel_robustness as cloth
from replay_first_grasp import POLICY
sys.path.insert(0,str(POLICY / "deployment_entry"))
from fold_sweep_feedback import SweepMonitor


DT = cloth.TIMESTEP
OBS_TICKS = round(.033 / DT)
FIELD_AXIS_TO_SCENE = np.array([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])
MODES = ("recorded", "recovery_arc", "recovery_lift_first")
FIELD = {}


@dataclass(frozen=True)
class Scenario:
    seed: int
    recording: str
    size: int
    friction: float = .9
    mass: float = .075
    shift_mm: float = 0.
    yaw_deg: float = 0.
    total_skew_mm: float = 0.
    feedback_noise_mm: float = 1.0
    tracking_tau_s: float = .027
    holding_force_n: float = 3.0
    grip_fraction: float = .64
    press_fraction: float = .43
    fault: str = "none"
    first_fold_setup: str = "rolled"
    left_contact: str = "phase"
    arc_height_m: float = .148
    arc_duration_s: float = 1.70
    pad_release_share: float = .50
    overtravel_mm: float = 0.
    visual_release: bool = False


def initialise(recordings):
    global FIELD
    FIELD = recordings
    cv2.setNumThreads(1)


def place_folded_strip(model, data, skew_mm):
    """Start the second-fold experiment with two settled layers and a free crease."""
    start = cloth.vertices(data).copy()
    desired = start.copy()
    upper = start[...,1] < -1e-6
    crease = np.abs(start[...,1]) <= 1e-6
    desired[...,1] = np.abs(start[...,1])
    desired[...,1] += upper * (.002 + .001*skew_mm
                              * (start[...,0]+.15)/.30 * (np.abs(start[...,1])/.10))
    desired[...,2] = np.where(upper,.0077,np.where(crease,.0048,.0025))
    for i, change in enumerate((desired-start).reshape(-1,3)):
        joints = model.body(f"towel_{i}").jntadr[0]
        begin = model.jnt_qposadr[joints]
        data.qpos[begin:begin+3] += change
    mujoco.mj_forward(model,data)


def fixture(scenario, rng):
    n = scenario.size
    m = round(n * 2 / 3) | 1
    cloth.NX, cloth.NY, cloth.SPACING = n, m, .30 / (n - 1)
    cloth.FIRST_EDGE = ((0, 0), (n - 1, 0))
    cloth.FAR_EDGE = ((0, m - 1), (n - 1, m - 1))
    half = m // 2
    side_indices = [j for j in range(1, half) if abs((m-1-j-half)/half - scenario.grip_fraction) < .29]
    if not side_indices:
        side_indices = [max(1, min(half-1, round(half*(1-scenario.grip_fraction))))]
    top, bottom = tuple((n-1, j) for j in side_indices), tuple((n-1, m-1-j) for j in side_indices)
    cloth.GRASP_POINTS = tuple(dict.fromkeys((*cloth.FIRST_EDGE, *top, *bottom)))
    model = cloth.scene(scenario.friction, scenario.mass, contact_probe=True)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    if scenario.first_fold_setup=="settled":
        place_folded_strip(model,data,scenario.total_skew_mm)
    cloth.transform_towel(model, data, np.array((scenario.shift_mm, .3*scenario.shift_mm))*.001,
                         scenario.yaw_deg)
    cloth.advance(model, data, .23)
    positions = cloth.vertices(data)
    if scenario.first_fold_setup=="rolled":
        near = np.array([positions[xy][:2] for xy in cloth.FIRST_EDGE])
        far = np.array([positions[xy][:2] for xy in cloth.FAR_EDGE])
        caught, _ = cloth.capture(model, data, cloth.FIRST_EDGE, near, heights=(.004,.004))
        if len(caught) != 2:
            raise RuntimeError("The first-fold fixture failed to obtain its setup grasp")
        far[1, 1] -= scenario.total_skew_mm * .001
        cloth.sweep_fold(model, data, cloth.FIRST_EDGE, np.column_stack((far,[.011,.011])),
                        height=.115, eased=True)
    original_errors = cloth.alignment(data)
    actual = np.array([positions[xy] for xy in (*top,*bottom)])
    camera = cloth.outline_observation(model, data, rng, scenario.feedback_noise_mm)
    right_goal = camera[0,1] + scenario.grip_fraction * (camera[1,1] - camera[0,1])
    # Place the nominal jaw at the observed side. Each captured vertex must
    # lie within the same aperture and the same effective patch of cloth.
    line = camera[1,1] - camera[0,1]
    line = line / np.linalg.norm(line)
    offsets = actual[:,:2] - right_goal
    across = np.abs(offsets @ np.array([-line[1],line[0]]))
    along = np.abs(offsets @ line)
    captured = (across<.014) & (along<.025) & (actual[:,2] < .023)
    if scenario.fault == "empty":
        captured[:] = False
    if scenario.fault == "top_only":
        captured[len(top):] = False
    pick = (*top,*bottom)
    active = [j for j,hit in enumerate(captured) if hit]
    if active:
        grips, entries = zip(*(cloth.grip_id(model,pick[j]) for j in active))
        grips, entries = list(grips), list(entries)
        start = actual[active]
        center = start.mean(axis=0)
        data.mocap_pos[grips] = start
        data.eq_active[entries] = True
    else:
        grips, entries, start = [], [], np.empty((0,3))
        center = np.r_[right_goal, .009]
    nx_top = sum(captured[:len(top)])
    nx_bottom = sum(captured[len(top):])
    pad = model.body("approach_probe").mocapid[0]
    # The left contact can press on the cloth while its fingers are unloaded.
    left_edge = camera[:,0].mean(axis=0)
    across_cloth = camera[:,1].mean(axis=0)-left_edge
    pad_xy = left_edge + scenario.press_fraction*across_cloth
    depth_line = camera[1,0]-camera[0,0]
    pad_xy += depth_line * .18
    data.mocap_pos[pad] = [*pad_xy,.055]
    for h in np.linspace(.055,.011,round(.18/DT)):
        data.mocap_pos[pad,2] = h
        mujoco.mj_step(model,data)
    latched = None
    if scenario.fault == "left_loaded":
        index = cloth.FIRST_EDGE[0]
        left, latched = cloth.grip_id(model,index)
        data.mocap_pos[left] = positions[index]
        data.eq_active[latched] = True
    return model,data,camera,grips,entries,start,center,pad,nx_top,nx_bottom,original_errors,latched


def ease(t):
    t = float(np.clip(t, 0., 1.))
    return t*t*t*(10.+t*(-15.+6.*t))


def proposed_position(mode, origin, landing, t, scenario):
    if mode == "recovery_arc":
        progress = ease(t / scenario.arc_duration_s)
        target = origin + (landing-origin)*progress
        target[2] += scenario.arc_height_m * math.sin(math.pi*progress)
        return target, progress
    lift = ease(t/.42)
    traverse = ease((t-.33)/1.20)
    lower = ease((t-1.35)/.54)
    point = origin + (landing-origin)*traverse
    point[2] = (origin[2] + .105*lift)*(1-lower) + landing[2]*lower
    return point, traverse


def receive_force(data, entries):
    if not entries:
        return 0.
    active = data.efc_type[:data.nefc] == mujoco.mjtConstraint.mjCNSTR_EQUALITY
    match = active & np.isin(data.efc_id[:data.nefc],entries)
    return float(np.linalg.norm(data.efc_force[:data.nefc][match]))


def execute(job):
    scenario, mode = job
    started = time.perf_counter()
    rng = np.random.default_rng(scenario.seed)
    (model,data,initial_view,grips,entries,pick,center,pad,top,bottom,first_errors,left_latch) = fixture(scenario,rng)
    source = FIELD[scenario.recording]
    saved_time = source["time"]
    raw = source["trajectory"]
    jaw_rotations = source.get("orientations")
    widths = source["widths"] if scenario.first_fold_setup=="rolled" else source["measured_widths"]
    if jaw_rotations is not None:
        jaw_rotations = np.array(jaw_rotations)
    maximum = min(12., float(saved_time[-1]))
    travel = float(np.linalg.norm(initial_view[:,1].mean(axis=0)-initial_view[:,0].mean(axis=0)))
    tolerance = scenario.pad_release_share * travel
    monitor = SweepMonitor(0., 0.)
    physical = center.copy()
    guard = None
    peak_load = 0.
    release_s = None
    drop_s = None
    excessive_samples = 0
    lateral = 0.
    pad_position = data.mocap_pos[pad].copy()
    pad_start = pad_position.copy()
    window_start = data.time
    rotation = np.eye(3)
    grip_motion = pick-center
    path = [center.copy()]
    moved = False
    used_landing = None
    aborted = None
    settled_views = 0
    best_visible_length = None
    for tick in range(round(maximum/DT)):
        elapsed = tick*DT
        sensor_valid = not (scenario.fault=="stale" and elapsed>3.0)
        sensor_valid &= not (guard is not None and scenario.fault=="late_dropout"
                             and elapsed-guard["time_s"]>.32)
        if guard is not None and (not sensor_valid or drop_s is not None):
            aborted = "feedback_stale" if not sensor_valid else "load_lost"
            reference = physical.copy()
        elif guard is not None:
            ago = elapsed-guard["time_s"]
            reference,traverse = proposed_position(mode, recovery_origin, recovery_destination, ago, scenario)
            rotation = np.eye(3)
            if mode == "recovery_lift_first":
                tilt = math.pi * traverse
                s,c = math.sin(tilt),math.cos(tilt)
                rotation = np.array([[c,0,s],[0,1,0],[-s,0,c]])
        else:
            recorded = np.array([np.interp(elapsed,saved_time,raw[:,i]) for i in range(3)])
            reference = center + FIELD_AXIS_TO_SCENE @ recorded
            reference[2] = max(.006,reference[2])
            if jaw_rotations is not None:
                index = min(len(saved_time)-1, np.searchsorted(saved_time,elapsed))
                rotation = jaw_rotations[index]
        right_under_load = release_s is None and drop_s is None and bool(entries)
        physical += (reference-physical) * min(1.,DT/scenario.tracking_tau_s)
        lateral = float((center[0]-physical[0])*1000)
        index = min(len(saved_time)-1,np.searchsorted(saved_time,elapsed))
        left_loaded = left_latch is not None or (guard is None and source["left_loaded"][index])
        pad_goal = pad_start.copy()
        if scenario.left_contact=="recorded" and guard is None and left_latch is None:
            pad_goal += FIELD_AXIS_TO_SCENE@source["left_measured"][index]
            pad_goal[2] = max(.009,pad_goal[2])
        elif lateral>1000*tolerance:
            pad_goal[2] = .08
        pad_position += (pad_goal-pad_position)*min(1.,DT/.08)
        data.mocap_pos[pad]=pad_position
        data.mocap_pos[grips] = physical + grip_motion@rotation.T
        mujoco.mj_step(model,data)
        if tick % OBS_TICKS != 0:
            continue
        path.append(physical.copy())
        force=receive_force(data,entries)
        peak_load=max(peak_load,force)
        if right_under_load:
            excessive_samples=excessive_samples+1 if force>scenario.holding_force_n else 0
            if excessive_samples>=4:
                drop_s=elapsed
                data.eq_active[entries]=False
        release_now=False
        if guard is None:
            release_now=np.interp(elapsed,saved_time,widths)>=18.
            if mode!="recorded":
                event=monitor.observe(elapsed, lateral+rng.normal(0,scenario.feedback_noise_mm),
                    right_loaded=right_under_load and force>.002, left_loaded=left_loaded,
                    valid=sensor_valid, right_released=release_now or release_s is not None,
                    time_left_s=maximum-elapsed)
                if event is not None and drop_s is None:
                    guard=event
                    recovery_origin=physical.copy()
                    current=cloth.outline_observation(model,data,rng,scenario.feedback_noise_mm)
                    target=current[0,0]+scenario.grip_fraction*(current[1,0]-current[0,0])
                    # Stay near the opposite edge observed at pinch. A local
                    # curl may otherwise move the outline across a fold line.
                    stationary_edge=initial_view[0,0]+scenario.grip_fraction*(initial_view[1,0]-initial_view[0,0])
                    target=stationary_edge + np.clip(target-stationary_edge,-.018,.018)
                    fold_direction=initial_view[:,0].mean(axis=0)-initial_view[:,1].mean(axis=0)
                    target += scenario.overtravel_mm*.001*fold_direction/travel
                    recovery_destination=np.r_[target,.014]
                    used_landing=recovery_destination.tolist()
        else:
            ago=elapsed-guard["time_s"]
            moved |= lateral>guard["progress_mm"]+22.
            settle_after=1.94 if mode=="recovery_lift_first" else scenario.arc_duration_s+.24
            release_now=(aborted is None and ago>=settle_after and lateral>=.80*1000*travel
                         and pad_position[2]>.04 and drop_s is None
                         and np.linalg.norm(physical-recovery_destination)<.020)
            if scenario.visual_release and release_now and release_s is None:
                observed = cloth.outline_observation(model,data,rng,scenario.feedback_noise_mm)
                visible_length = float(np.linalg.norm(observed[:,1].mean(axis=0)
                                                     -observed[:,0].mean(axis=0))/travel)
                if best_visible_length is None or visible_length < best_visible_length:
                    best_visible_length = visible_length
                settled_views = settled_views+1 if visible_length <= .72 else 0
                release_now = settled_views >= 3
            elif not release_now:
                settled_views = 0
        if release_s is None and release_now:
            release_s=elapsed
            data.eq_active[entries]=False
        if release_s is not None and elapsed-release_s>.66:
            break
    data.mocap_pos[pad,2]=.12
    if left_latch is not None:
        data.eq_active[left_latch]=False
    cloth.advance(model,data,.42)
    alignment=cloth.alignment(data)
    p=cloth.vertices(data)
    amount=float(np.ptp(p[...,0]))/max(travel,1e-6)
    final=float(data.time-window_start)
    peak_accel=float(np.max(np.linalg.norm(np.diff(path,n=2,axis=0),axis=1))/(OBS_TICKS*DT)**2) if len(path)>3 else 0.
    return dict(case=asdict(scenario), mode=mode, initial_alignment_mm=first_errors,
                final_alignment_mm=alignment, field_outcome=source["label"], top_grip_nodes=int(top),
                lower_grip_nodes=int(bottom), recovery=guard, recovery_motion=bool(moved), landing_m=used_landing,
                recovery_aborted=aborted,
                visual_length_before_release=best_visible_length,
                released_at_s=release_s, load_lost_at_s=drop_s, peak_constraint_load_n=peak_load,
                final_length_fraction=amount, simulated_seconds=final, worker_wall_s=time.perf_counter()-started,
                peak_observed_accel_m_s2=peak_accel, success=(release_s is not None and drop_s is None
                    and max(alignment)<=28. and amount<=.67))


def read_trajectories(path, include_orientation):
    from first_grasp_approach import jaw_pose
    from fold_sweep_feedback import loaded_grip

    runs = {}
    for row in json.loads(path.read_text())["runs"]:
        if not row.get("trajectory"):
            continue
        trajectory=row["trajectory"]
        result=dict(label=row["label"], time=np.asarray(trajectory["elapsed_s"]),
                    trajectory=np.asarray(trajectory["target_jaw_delta_mm"])*.001,
                    widths=np.asarray(trajectory["target_width_mm"]),
                    measured_widths=np.asarray(trajectory["measured_gripper_width_effort"])[:,0])
        trace=json.loads(Path(row["trace"]).read_text())["rows"][row["regrasp_step"]:]
        left_pose=jaw_pose(np.array([r["measured"][:6] for r in trace]))[:,:3]
        result["left_measured"]=(left_pose-left_pose[0])*.001
        result["left_loaded"]=[]
        for saved in trace:
            left=(saved.get("gripper_feedback_before_action") or {}).get("left") or {}
            result["left_loaded"].append(
                left.get("enabled") is True and left.get("fault_bits")=="0x0"
                and loaded_grip(left,1000*saved["previous"][6],minimum_width_mm=2.5))
        if include_orientation:
            vector=jaw_pose(np.array([r["target"][7:13] for r in trace]))[:,3:]
            orientation=(vector.reshape(-1,3,3)/45.)
            E=FIELD_AXIS_TO_SCENE
            rotations=E@orientation@orientation[0].T@E.T
            result["orientations"]=rotations
        if result["time"][-1]>5.0:
            runs[row["name"]]=result
    return runs


def build_cases(trajectories, suite, count, size, seed, first_fold_setup, left_contact):
    if suite=="reference":
        for i,name in enumerate(trajectories):
            yield Scenario(seed+i,name,size,first_fold_setup=first_fold_setup,left_contact=left_contact)
        return
    rng=np.random.default_rng(seed)
    failed=[k for k,v in trajectories.items() if v["label"]=="loaded_incomplete"]
    passed=[k for k,v in trajectories.items() if v["label"]=="completed"]
    for i in range(count):
        bucket=failed if i%3 else passed
        name=bucket[(i//3)%len(bucket)]
        fault=("none",)*7+("empty","stale","left_loaded","top_only","late_dropout")
        yield Scenario(seed+i,name,size,friction=float(rng.uniform(.61,1.10)),mass=float(rng.uniform(.055,.100)),
                       shift_mm=float(rng.uniform(-17,17)),yaw_deg=float(rng.uniform(-6,6)),
                       total_skew_mm=float(rng.uniform(-13,13)),feedback_noise_mm=float(rng.uniform(.3,2.5)),
                       tracking_tau_s=float(rng.uniform(.016,.055)),holding_force_n=float(rng.uniform(1.,3.4)),
                       grip_fraction=float(rng.uniform(.57,.76)),press_fraction=float(rng.uniform(.37,.49)),
                       fault=fault[i%len(fault)],first_fold_setup=first_fold_setup,left_contact=left_contact)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--suite",choices=("reference","tuning","holdout"),default="reference")
    parser.add_argument("--size",type=int,choices=(13,17,21),default=13)
    parser.add_argument("--count",type=int,default=60)
    parser.add_argument("--seed",type=int,default=20261120)
    parser.add_argument("--workers",type=int,default=8)
    parser.add_argument("--mode",choices=MODES,action="append")
    parser.add_argument("--no-orientation",action="store_true")
    parser.add_argument("--first-fold-setup",choices=("rolled","settled"),default="rolled")
    parser.add_argument("--left-contact",choices=("phase","recorded"),default="phase")
    args=parser.parse_args()
    modes=tuple(dict.fromkeys(args.mode or MODES))
    trajectories=read_trajectories(args.audit,not args.no_orientation)
    cases=list(build_cases(trajectories,args.suite,args.count,args.size,args.seed,
                           args.first_fold_setup,args.left_contact))
    tasks=[(case,mode) for case in cases for mode in modes]
    start=time.perf_counter()
    results=[]
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with ProcessPoolExecutor(args.workers,initializer=initialise,initargs=(trajectories,)) as pool, \
         args.out.with_suffix(".jsonl").open("w",buffering=1) as journal:
        for result in pool.map(execute,tasks):
            results.append(result)
            journal.write(json.dumps(result,allow_nan=False)+"\n")
            if len(results)%12==0:
                print(f"{len(results)}/{len(tasks)} completed; {time.perf_counter()-start:.1f}s wall",flush=True)
    summary={}
    for mode in modes:
        rows=[r for r in results if r["mode"]==mode]
        by_label={}
        for label in ("completed","loaded_incomplete"):
            group=[r for r in rows if r["field_outcome"]==label and r["case"]["fault"]=="none"]
            by_label[label]=dict(count=len(group),passed=sum(r["success"] for r in group),
                median_final_mm=float(np.median([max(r["final_alignment_mm"]) for r in group])) if group else None)
        negatives=[r for r in rows if r["case"]["fault"] in ("empty","stale","left_loaded")]
        assert not any(r["recovery_motion"] for r in negatives)
        dropouts = [r for r in rows if r["case"]["fault"]=="late_dropout" and r["recovery"]]
        assert all(r["recovery_aborted"] in ("feedback_stale","load_lost") and r["released_at_s"] is None
                   for r in dropouts)
        layers = [r for r in rows if r["case"]["fault"]=="top_only"]
        summary[mode]=dict(cases=len(rows), success=sum(r["success"] for r in rows),
                          interventions=sum(r["recovery"] is not None for r in rows),
                          negatives=len(negatives),negative_recovery_motion=0,
                          dropout_aborts=len(dropouts),
                          single_layer_interventions=sum(r["recovery"] is not None for r in layers),
                          by_label=by_label)
    report=dict(suite=args.suite, seed=args.seed, first_fold_setup=args.first_fold_setup,
                left_contact=args.left_contact,
                wall_seconds=time.perf_counter()-start,
                solver_worker_wall_seconds=sum(r["worker_wall_s"] for r in results),
                simulated_seconds=sum(r["simulated_seconds"] for r in results), robot_commands_sent=False,
                scope="Recorded joint targets mapped by Piper FK to a relative wrist trajectory. "
                      "The actual robot bases, camera and gripper forces are uncalibrated. "
                      "Two cloth layers use tunable attach/slip approximation; a collidable left press models support. "
                      "The candidate re-observes the simulated outline when it detects a stall. "
                      "The learned policy is not rerun from synthetic images.",
                mujoco_version=mujoco.__version__, summary=summary,results=results)
    args.out.write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
    print(json.dumps(summary),flush=True)


if __name__=="__main__":
    main()
