"""Measure the first-fold crease and exposed flap from saved middle-camera frames."""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from field_table_geometry import SEGMENTATION, cloth_features, table_coordinates
from replay_first_grasp import POLICY, read_json, read_jsonl


def find_profile(rgb):
    feature = cloth_features(rgb, SEGMENTATION)
    if feature is None:
        return None
    mask = feature["mask"] > 0
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    blue = ((hsv[:, :, 0] >= 85) & (hsv[:, :, 0] <= 135) & (hsv[:, :, 1] >= 55))
    columns = np.flatnonzero(mask.sum(axis=0) >= 65)
    if columns.size < 200:
        return None
    span = np.percentile(columns, [3, 97])
    x_positions = np.rint(np.linspace(*span, 13)[1:-1]).astype(int)
    edges, scores, allowed = [], [], []
    for x in x_positions:
        strip = slice(x-11, x+12)
        support = mask[:, strip].sum(axis=1)
        visible = np.flatnonzero(support >= 15)
        if len(visible) < 65:
            return None
        top, bottom = int(visible[0]), int(visible[-1])
        value = np.median(hsv[:, strip, 2], axis=1)
        luminance = np.convolve(value, np.ones(5)/5, "same")
        signal = .5*(np.roll(luminance, 11)+np.roll(luminance, -11))-luminance
        start = top+int(.24*(bottom-top))
        end = top+int(.79*(bottom-top))
        reliable = (blue[:, strip].mean(axis=1) >= .48)
        limits = (np.arange(480) >= start) & (np.arange(480) <= end) & reliable
        if not limits.any():
            limits[start:end+1] = True
            signal.fill(-5.)
        edges.append((top, bottom))
        scores.append(signal)
        allowed.append(limits)

    # Fit a continuous internal seam across independently sampled strips.
    distance = np.abs(np.subtract.outer(np.arange(480), np.arange(480)))
    transition = .60 * distance + .03 * distance**2
    best = np.where(allowed[0], scores[0], -1.e6)
    predecessors = []
    for signal, limits in zip(scores[1:], allowed[1:], strict=True):
        links = best[:, None]-transition
        predecessors.append(links.argmax(axis=0))
        best = links.max(axis=0) + np.where(limits, signal, -1.e6)
    seam = [int(best.argmax())]
    for index in reversed(predecessors):
        seam.append(int(index[seam[-1]]))
    seam.reverse()
    response = [float(signal[y]) for signal, y in zip(scores, seam, strict=True)]
    return dict(points_px=np.column_stack((x_positions, np.asarray(edges)[:,0], seam,
                                           np.asarray(edges)[:,1])).tolist(),
                seam_response=response, area_px=float(feature["area"]), contour_px=feature["contour"])


def measure_frame(rgb, table, initial_area):
    profile = find_profile(rgb)
    if profile is None:
        return None
    x, crease, lip, near = np.asarray(profile["points_px"]).T
    hinge, free, bottom = (table_coordinates(np.column_stack((x, y)), table)[:, :2]
                           for y in (crease, lip, near))
    x_boundary = table_coordinates(np.asarray(profile.pop("contour_px")), table)[:,0]
    full = (hinge[:,1]-bottom[:,1])*1000.
    flap = (hinge[:,1]-free[:,1])*1000.
    uncovered = full-flap
    area_ratio = profile["area_px"]/initial_area
    strong = np.asarray(profile["seam_response"]) >= 5.
    valid = bool(.47 <= area_ratio <= .83 and strong.mean() >= .64 and
                 (full > 68.).all() and (uncovered > 12.).all())
    return dict(**profile, area_ratio=area_ratio, reliable=valid, strong_strips=int(strong.sum()),
                folded_center_x_m=float((x_boundary.min()+x_boundary.max())/2),
                hinge_table_m=hinge.tolist(), flap_lip_table_m=free.tolist(), bottom_edge_table_m=bottom.tolist(),
                bottom_span_mm=full.tolist(), top_flap_span_mm=flap.tolist(), uncovered_strip_mm=uncovered.tolist())


def evaluate(recording, rows, steps, table):
    frames = read_jsonl(recording / "frame_times.jsonl")
    frame_times = np.asarray([row["monotonic_s"] for row in frames])
    row_map = {row["step"]: row["tick_monotonic_s"] for row in rows}
    initial = cv2.cvtColor(cv2.imread(str(recording/"first_middle.png")), cv2.COLOR_BGR2RGB)
    initial_area = cloth_features(initial, SEGMENTATION)["area"]
    samples, marked = [], []
    video = cv2.VideoCapture(str(recording/"middle.avi"))
    try:
        for step in steps:
            tick = row_map[step]
            index = int(np.argmin(np.abs(frame_times-tick)))
            if abs(frame_times[index]-tick) > .1:
                continue
            video.set(cv2.CAP_PROP_POS_FRAMES, frames[index]["video_frame_index"])
            ok, image = video.read()
            if not ok:
                continue
            evidence = measure_frame(cv2.cvtColor(image, cv2.COLOR_BGR2RGB), table, initial_area)
            if evidence is None:
                continue
            for i, color in zip((1, 2, 3), ((0,220,110),(30,210,255),(255,160,55)), strict=True):
                xy = np.asarray(evidence["points_px"])[:, [0,i]].astype(np.int32)
                cv2.polylines(image, [xy], False, color, 2, cv2.LINE_AA)
            text = f"{step}: {np.median(evidence['uncovered_strip_mm']):.0f} mm" if evidence["reliable"] else f"{step}: low confidence"
            cv2.putText(image,text,(10,25),cv2.FONT_HERSHEY_SIMPLEX,.65,(230,230,230),2,cv2.LINE_AA)
            marked.append(image)
            samples.append(dict(step=step, **evidence))
    finally:
        video.release()
    reliable = [sample for sample in samples if sample["reliable"]]
    if len(reliable) >= 2:
        spans = [np.median(sample["uncovered_strip_mm"]) for sample in reliable]
        median_span = np.median(spans)
        reliable = [sample for sample, span in zip(reliable, spans, strict=True)
                    if abs(span - median_span) <= 15.]
    summary = dict(samples=samples, usable_frames=len(reliable), fitted_from_video=bool(len(reliable)>=2))
    if len(reliable) >= 2:
        chosen = min(reliable, key=lambda sample: abs(sample["step"]-np.median(steps)))
        summary["reference_frame"] = chosen
        for key in ("bottom_span_mm","top_flap_span_mm","uncovered_strip_mm"):
            per_frame = [float(np.median(sample[key])) for sample in reliable]
            summary[key] = dict(median=float(np.median(per_frame)),
                                frame_range=[float(min(per_frame)), float(max(per_frame))])
    return summary, marked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    table = read_json(args.table)["table"]
    args.output.mkdir(parents=True, exist_ok=True)
    results = {}
    for name, case in read_json(args.manifest).items():
        trace = POLICY / "logs" / case["trace"] / "trace.json"
        rec = POLICY / "logs" / case["recording"]
        result, frames = evaluate(rec, read_json(trace)["rows"], case["measure_steps"], table)
        results[name] = result
        if frames:
            cv2.imwrite(str(args.output/f"{name}_layers.jpg"), np.concatenate(frames,axis=1))
        print(name, result["usable_frames"],result.get("uncovered_strip_mm"),flush=True)
    (args.output/"observed_folded_layers.json").write_text(json.dumps(results,indent=2,allow_nan=False)+"\n")


if __name__ == "__main__":
    main()
