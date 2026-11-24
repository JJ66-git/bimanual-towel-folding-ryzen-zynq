#!/usr/bin/env python3
"""Show one complete MuJoCo cloth-folding cycle in the native viewer."""

import argparse
import json
from pathlib import Path
import threading
import time

import mujoco
import mujoco.viewer
import numpy as np

from piper_cycle_display import JOINT_MAX_SPEED_RAD_S, PiperDisplay
from simulate_towel_robustness import Case, MODES, trial


STATE = mujoco.mjtState.mjSTATE_INTEGRATION


class CycleViewer:
    def __init__(self, speed):
        self.speed = speed
        self.viewer = None
        self.model = self.data = None
        self.frames = []
        self.replay_requested = threading.Event()
        self.piper = PiperDisplay()

    def handle_key(self, key):
        if key == ord("R"):
            self.replay_requested.set()

    def open(self, model, data):
        self.model, self.data = model, data
        self.viewer = mujoco.viewer.launch_passive(model, data, key_callback=self.handle_key,
                                                   show_left_ui=False, show_right_ui=False)
        with self.viewer.lock():
            self.viewer.cam.lookat[:] = (0., -.12, .135)
            self.viewer.cam.azimuth = -83.
            self.viewer.cam.elevation = -31.
            self.viewer.cam.distance = 1.20
        print("MUJOCO_VIEWER_OPEN", flush=True)

    def draw(self, stage):
        if not self.viewer.is_running():
            return
        with self.viewer.lock():
            scene = self.viewer.user_scn
            scene.ngeom = 0
            gaps = [1000. * (self.data.qpos[q[-2]] - self.data.qpos[q[-1]])
                    for q, *_ in self.piper.indices]
            caption = self.geom(scene, mujoco.mjtGeom.mjGEOM_LABEL, (0, 0, 0),
                                (-.10, .20, .030), (.95, .95, .95, 1.))
            caption.label = stage + ("  |  R: replay" if stage.startswith("Cycle") else "")
            aperture = self.geom(scene, mujoco.mjtGeom.mjGEOM_LABEL, (0, 0, 0),
                                 (-.10, .20, .012), (.95, .95, .95, 1.))
            aperture.label = f"L {gaps[0]:.1f} mm  |  R {gaps[1]:.1f} mm"
        self.viewer.sync()

    @staticmethod
    def geom(scene, shape, size, position, color):
        geom = scene.geoms[scene.ngeom]
        mujoco.mjv_initGeom(geom, shape, size, position, np.eye(3).ravel(), color)
        scene.ngeom += 1
        return geom

    def record(self, model, data, stage, tools, closed):
        previous = self.frames[-1] if self.frames else None
        if previous is not None and previous[1] == stage and data.time - previous[0] < 1 / 35:
            return
        self.piper.track(data, stage, tools, closed)
        snapshot = np.empty(mujoco.mj_stateSize(model, STATE))
        mujoco.mj_getState(model, data, snapshot, STATE)
        frame = (data.time, stage, snapshot, self.piper.q.copy())
        if self.viewer is None:
            self.open(model, data)
        self.frames.append(frame)
        if self.viewer.is_running():
            self.wait_for_frame(previous, frame)
            self.draw(stage)

    def wait_for_frame(self, previous, frame):
        if previous is None:
            time.sleep(.9)
            return
        joint_seconds = float(np.max(np.abs(frame[3] - previous[3]) / JOINT_MAX_SPEED_RAD_S))
        seconds = max((frame[0] - previous[0]) / self.speed, joint_seconds)
        if previous is not None and frame[1] != previous[1]:
            seconds += {"First grasp": .28, "Second grasp": .28, "Second release": .15}.get(previous[1], 0.)
        time.sleep(seconds)

    def keep_open(self):
        while self.viewer.is_running():
            if not self.replay_requested.wait(.1):
                self.viewer.sync()
                continue
            self.replay_requested.clear()
            previous = None
            for frame in self.frames:
                if not self.viewer.is_running():
                    return
                self.wait_for_frame(previous, frame)
                _, stage, state, _ = frame
                mujoco.mj_setState(self.model, self.data, state, STATE)
                mujoco.mj_forward(self.model, self.data)
                self.draw(stage)
                previous = frame


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--out", type=Path, required=True)
    args.add_argument("--mode", choices=MODES, default="both_retarget_low_second_lay")
    args.add_argument("--speed", type=float, default=.65)
    args.add_argument("--offset-x-mm", type=float, default=0.)
    args.add_argument("--offset-y-mm", type=float, default=0.)
    args.add_argument("--yaw-deg", type=float, default=0.)
    settings = args.parse_args()
    if not np.isfinite(settings.speed) or settings.speed <= 0:
        args.error("speed must be positive and finite")
    viewer = CycleViewer(settings.speed)
    case = Case("visual_cycle", settings.offset_x_mm, settings.offset_y_mm, settings.yaw_deg)
    try:
        result = trial((case, 1763, settings.mode), full_cycle=True, on_frame=viewer.record,
                       model_builder=viewer.piper.build)
        report = dict(mujoco_version=mujoco.__version__, robot_commands_sent=False,
                      piper=viewer.piper.report(),
                      scope="Deformable towel, PiPER visual kinematics and constraint-based grasp", **result)
        settings.out.parent.mkdir(parents=True, exist_ok=True)
        settings.out.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        print("MUJOCO_CYCLE_RESULT=" + json.dumps(report), flush=True)
        viewer.keep_open()
        return int(not result["cycle_complete"])
    finally:
        if viewer.viewer is not None:
            viewer.viewer.close()


if __name__ == "__main__":
    raise SystemExit(main())
