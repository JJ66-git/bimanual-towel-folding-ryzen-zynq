"""Compare right-jaw closure locations with both visible layers in saved runs."""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from field_table_geometry import project
from replay_field_fold import frame_kinematics, run_from_manifest
from replay_first_grasp import read_json, read_jsonl


def approach_episodes(rows, start_step, positions):
    """Return approach closures with the nearby feedback and observed pose."""
    ready = False
    for index, row in enumerate(rows):
        if row["step"] < start_step:
            continue
        right = row["gripper_feedback_before_action"]["right"]
        ready |= right["width_mm"] >= 27. and row["target"][13] >= .024
        if not ready or row["target"][13] >= .012:
            continue
        ready = False
        end = min(len(rows), index + 12)
        widths = [rows[j]["gripper_feedback_before_action"]["right"]["width_mm"]
                  for j in range(index, end)]
        if not widths or min(widths) >= 12.:
            continue
        nearest = min(range(index, end), key=lambda j: abs(widths[j-index] - 6.))
        if positions[nearest,2] > .085 or positions[nearest,0] < .12:
            continue
        feedback = [item["gripper_feedback_before_action"]["right"] for item in rows[index:index+24]]
        median_effort = float(np.median([-sample["effort_Nm"] for sample in feedback]))
        yield index, nearest, median_effort


def compare(root, label, observation, output):
    run = run_from_manifest(root, label)
    table = read_json(root/"table_geometry.json")["table"]
    rows = read_json(run.trace)["rows"]
    mounts = read_json(root/"estimated_mounts.json")
    _, jaws, _ = frame_kinematics(rows, mounts)
    hinge, flap = (np.asarray(observation[f]) for f in ("hinge_table_m", "flap_lip_table_m"))
    stamps = read_jsonl(run.recording/"frame_times.jsonl")
    times = np.asarray([stamp["monotonic_s"] for stamp in stamps])
    cameras = [cv2.VideoCapture(str(run.recording/f"{role}.avi")) for role in ("middle", "right")]
    out, tiles = [], []
    try:
        for begin, sample, median_effort in approach_episodes(rows, observation["step"], jaws[:,1]):
            x, y, z = jaws[sample,1]
            near = float(np.interp(x, flap[:,0], flap[:,1]))
            far = float(np.interp(x, hinge[:,0], hinge[:,1]))
            # Positive on the projected strip where the two visible layers overlap.
            offset = min(y-near, far-y)*1000.
            closest_frame = int(np.argmin(np.abs(times-rows[sample]["tick_monotonic_s"])))
            report = dict(close_requested_step=rows[begin]["step"], evaluated_step=rows[sample]["step"],
                          median_closing_effort_Nm=median_effort,
                          estimated_xyz_table_mm=(jaws[sample,1]*1000.).tolist(),
                          signed_overlap_margin_mm=float(offset),
                          frame_delta_ms=float(1000*(times[closest_frame]-rows[sample]["tick_monotonic_s"])))
            out.append(report)
            frames = []
            for capture in cameras:
                capture.set(cv2.CAP_PROP_POS_FRAMES, stamps[closest_frame]["video_frame_index"])
                ok, frame = capture.read()
                if not ok:
                    break
                frames.append(frame)
            if len(frames) == len(cameras):
                p = jaws[sample,1][None,:] @ np.asarray(table["table_to_camera_rotation"]).T + table["table_origin_camera_m"]
                uv = tuple(np.rint(project(p,table["color_intrinsics"])[0]).astype(int))
                cv2.drawMarker(frames[0], uv, (50,200,255), cv2.MARKER_CROSS, 19, 2)
                pair = np.concatenate([cv2.resize(frame,(480,360)) for frame in frames],axis=1)
                pair = cv2.copyMakeBorder(pair,0,32,0,0,cv2.BORDER_CONSTANT,value=(28,31,36))
                cv2.putText(pair,f"{label}  |  {rows[sample]['step']}  |  tool height {z*1000:.0f} mm  |  effort {median_effort:.2f} Nm",
                            (9,381),cv2.FONT_HERSHEY_SIMPLEX,.52,(235,239,243),1,cv2.LINE_AA)
                tiles.append(pair)
    finally:
        for capture in cameras:
            capture.release()
    if tiles:
        cv2.imwrite(str(output/f"{label}_regrasp.jpg"),np.concatenate(tiles,axis=0))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    output = args.root/"wrist_comparison"
    output.mkdir(exist_ok=True)
    observed = read_json(args.root/"observed_folded_layers.json")
    reports = {name:compare(args.root,name,case["reference_frame"],output)
               for name,case in observed.items() if case["fitted_from_video"]}
    (args.root/"right_regrasp_geometry.json").write_text(json.dumps(dict(runs=reports,
        scope="Static post-first-fold overlap projected onto the table, approximate jaw pose from the recorded mount fit. "
              "Later cloth motion and layer contact are separate observations.",robot_commands_sent=False),
        indent=2, allow_nan=False)+"\n")


if __name__ == "__main__":
    main()
