#!/usr/bin/env python3
"""Check candidate wrist sweeps against the local Piper joint envelope offline."""

import argparse
import json
from pathlib import Path
import sys

import cv2
import numpy as np

POLICY = (Path(__file__).resolve().parents[1] / "amd_hybrid_deploy_20261031/piper_towel_replica")
sys.path.insert(0, str(POLICY / "deployment_entry"))
from first_grasp_approach import jaw_pose


def ease(fraction):
    f = np.clip(fraction,0.,1.)
    return f**3*(10 + f*(-15 + 6*f))


def matrix(joints):
    return jaw_pose(joints)[3:].reshape(3,3)/45.


def fit_pose(start, target, lower, upper):
    command = start.copy()
    weights = np.r_[np.ones(3), np.full(9,.23)]
    perturb = np.eye(6)*1e-4
    for _ in range(16):
        pose = jaw_pose(command)
        error = (target-pose)*weights
        jacobian = (((jaw_pose(command+perturb)-pose)*weights)/1e-4).T
        augmented = np.vstack((jacobian,.65*np.eye(6)))
        proposal = np.linalg.lstsq(augmented,np.r_[error,np.zeros(6)],rcond=None)[0]
        proposal = np.clip(proposal,-.08,.08)
        old_error = np.linalg.norm(error)
        for scale in (1.,.5,.2):
            candidate = np.clip(command+scale*proposal,lower,upper)
            if np.linalg.norm((target-jaw_pose(candidate))*weights)<old_error-1e-8:
                command = candidate
                break
        else:
            break
    return command


def sweep(row, reference, lower, upper, *, duration, advance_mm, raise_mm, rotate):
    initial = np.asarray(row["measured"][7:13])
    start = jaw_pose(initial)
    origin = start[:3]
    start_rotation = matrix(initial)
    goal_rotation = matrix(reference["target"][7:13]) if rotate else start_rotation
    axis, _ = cv2.Rodrigues(goal_rotation @ start_rotation.T)
    requested = np.array([[-15.,advance_mm,8.]])
    t = np.arange(1, round(duration*30)+1)/30.
    progress = ease((t-.32)/(duration-.68))
    lifted = ease(t/.48)*(1-ease((t-duration+.52)/.52))
    waypoints = origin + progress[:,None] * requested + np.column_stack(
        (np.zeros_like(t),np.zeros_like(t),raise_mm*lifted))
    orientations = [cv2.Rodrigues(axis*fraction)[0] @ start_rotation for fraction in progress]
    command = np.clip(initial,lower,upper)
    joints = [command.copy()]
    position_errors, orientation_errors = [], []
    for target, target_matrix in zip(waypoints, orientations, strict=True):
        command = fit_pose(command,np.r_[target,(45*target_matrix).ravel()],lower,upper)
        joints.append(command.copy())
        actual = jaw_pose(command)
        position_errors.append(float(np.linalg.norm(actual[:3]-target)))
        relative = actual[3:].reshape(3,3)/45. @ target_matrix.T
        cos_error = (np.trace(relative)-1.)/2.
        orientation_errors.append(float(np.rad2deg(np.arccos(np.clip(cos_error,-1.,1.)))))
    joints = np.asarray(joints)
    max_step = float(np.max(np.abs(np.diff(joints,axis=0))))
    max_error = max(position_errors)
    max_rot = max(orientation_errors)
    return dict(duration_s=duration,requested_lateral_mm=advance_mm,lift_mm=raise_mm,
                rotating_to_observed_release=rotate,max_joint_step_rad=max_step,
                max_tcp_error_mm=max_error,max_orientation_error_deg=max_rot,
                tcp_step_p95_mm=float(np.percentile(np.linalg.norm(np.diff(waypoints,axis=0),axis=1),95)),
                minimum_joint_envelope_margin_rad=float(np.min(np.minimum(joints-lower,upper-joints))),
                interpolation_passed=max_step<=.05 and max_error<=4. and max_rot<=14.)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    runs={run["name"]:run for run in json.loads(args.audit.read_text())["runs"]}
    selected=runs["current_1021"]
    current=json.loads(Path(selected["trace"]).read_text())["rows"]
    reference=runs["second_fold_check_run_231447"]
    recorded=json.loads(Path(reference["trace"]).read_text())["rows"]
    release=recorded[reference["first_target_open_step"]]
    config=json.loads((POLICY/"configs/cobot_motion.json").read_text())
    lo=np.asarray(config["software_lower"][7:13])
    hi=np.asarray(config["software_upper"][7:13])
    cases=[]
    for duration in (2.15,2.75,3.35):
        for travel in (170.,205.,240.):
            for lift in (55.,85.,120.,150.):
                for rotate in (False,True):
                    cases.append(sweep(current[525],release,lo,hi,duration=duration,
                                       advance_mm=travel,raise_mm=lift,rotate=rotate))
    result=dict(robot_commands_sent=False, anchor_step=525, reference_step=release["step"],
                starting_nominal_tcp_mm=jaw_pose(np.array(current[525]["measured"][7:13]))[:3].tolist(),
                scope="SDK tool-proxy IK only, with the site joint envelope. "
                      "No estimation of inter-arm or table collision clearance.",
                passed=sum(row["interpolation_passed"] for row in cases), cases=cases)
    args.out.write_text(json.dumps(result,indent=2,allow_nan=False)+"\n")
    print(result["passed"],"/",len(cases),"kinematically feasible by this screen.",flush=True)


if __name__=="__main__":
    main()
