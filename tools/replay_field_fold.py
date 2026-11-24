"""Drive a 48 by 25 cm MuJoCo towel with saved PiPER feedback and measured table coordinates."""

import argparse
from bisect import bisect_left
from dataclasses import dataclass
import json
from pathlib import Path
import threading
import time

import cv2
import mujoco
import numpy as np

from field_table_geometry import SEGMENTATION, cloth_features, project
from piper_cycle_display import PiperDisplay, SIDES
from replay_first_grasp import POLICY, read_json


TOWEL_SIZE = np.array((.48, .25))
DT = .003333333333
PAD_HALF_SIZE = (.012, .0025, .016)


@dataclass(frozen=True)
class Run:
    trace: Path
    recording: Path
    label: str
    first_eval_step: int
    second_eval_step: int


@dataclass(frozen=True)
class Material:
    bending_modulus_pa: float = 0.
    damping_s: float = 0.
    discrete: bool = False


def run_from_manifest(root, name):
    item = read_json(root / "field_runs.json")[name]
    return Run(POLICY / "logs" / item["trace"] / "trace.json",
               POLICY / "logs" / item["recording"], item["label"],
               item["first_eval_step"], item["second_eval_step"])


def frame_kinematics(rows, mounts):
    xml = PiperDisplay.add_arms('<mujoco model="field_arms"><worldbody/></mujoco>', mounts)
    model = mujoco.MjModel.from_xml_string(xml)
    data = mujoco.MjData(model)
    measured = np.array([row["measured"] for row in rows])
    jaws = np.empty((len(rows), 2, 3))
    rotations = np.empty((len(rows), 2, 4))
    for arm, side in enumerate(SIDES):
        addresses = [model.joint(f"{side}_joint{i}").qposadr[0] for i in range(1, 7)]
        offset = np.array(mounts[side]["visual_point_in_link6_m"])
        for index, row in enumerate(measured):
            data.qpos[addresses] = row[arm*7:arm*7+6]
            mujoco.mj_kinematics(model, data)
            wrist = data.body(f"{side}_link6")
            matrix = wrist.xmat.reshape(3, 3)
            jaws[index, arm] = wrist.xpos + matrix @ offset
            mujoco.mju_mat2Quat(rotations[index, arm], wrist.xmat)
            if index and rotations[index, arm] @ rotations[index-1, arm] < 0:
                rotations[index, arm] *= -1
    return measured, jaws, rotations


def rotation(theta):
    s, c = np.sin(theta), np.cos(theta)
    return np.array(((c, -s), (s, c)))


def initial_grid(outline, nx, ny):
    corners = np.asarray(outline["corners_table_m"])
    edges = np.roll(corners, -1, axis=0) - corners
    axis = edges[np.linalg.norm(edges, axis=1).argmax()]
    if axis[0] < 0:
        axis = -axis
    yaw = np.arctan2(axis[1], axis[0])
    spacing = TOWEL_SIZE / (np.array((nx, ny)) - 1)
    x, y = np.meshgrid(np.linspace(-TOWEL_SIZE[0]/2, TOWEL_SIZE[0]/2, nx),
                       np.linspace(-TOWEL_SIZE[1]/2, TOWEL_SIZE[1]/2, ny), indexing="ij")
    original = np.column_stack((x.ravel(), y.ravel(), np.full(nx*ny, .005)))
    return original, spacing, np.asarray(outline["center_table_m"]), yaw


def observed_lay(original, outline, center, yaw):
    """Use the measured outline to shape the edges while retaining material size."""
    if "contour_table_m" not in outline:
        return original[:, :2] @ rotation(yaw).T + center
    contour = (np.asarray(outline["contour_table_m"]) - center) @ rotation(yaw)
    mid = (contour.min(axis=0) + contour.max(axis=0)) * .5
    contour = (contour - mid) * (TOWEL_SIZE / np.ptp(contour, axis=0))
    following = np.roll(contour, -1, axis=0)
    placed = original[:, :2].copy()
    for i, site in enumerate(original[:, :2]):
        for axis in (0, 1):
            along = 1-axis
            value = np.clip(site[along], contour[:,along].min()+.002, contour[:,along].max()-.002)
            crossing = (contour[:, along] > value) != (following[:, along] > value)
            first, last = contour[crossing], following[crossing]
            cut = first[:, axis] + (last[:, axis]-first[:, axis]) * (
                  (value-first[:, along]) / (last[:, along]-first[:, along]))
            if cut.size < 2:
                continue
            fraction = site[axis]/TOWEL_SIZE[axis] + .5
            target = cut.min()*(1.-fraction) + cut.max()*fraction
            placed[i, axis] += np.clip(target-site[axis], -.012, .012)
    return placed @ rotation(yaw).T + center


def place_observed_first_fold(original, center, observation, thickness, crease_shift_mm=0.):
    """Reconstruct both material layers from the outline and the observed lip."""
    hinge, lip, near = [np.asarray(observation[key]) for key in (
        "hinge_table_m", "flap_lip_table_m", "bottom_edge_table_m")]
    center_x = observation.get("folded_center_x_m", center[0])
    x = original[:,0] + center_x
    crease = np.interp(x, hinge[:,0], hinge[:,1])
    lower_y = np.interp(x, near[:,0], near[:,1])
    upper_y = np.interp(x, lip[:,0], lip[:,1])
    bottom_span = crease - lower_y
    flap_span = crease - upper_y
    if not ((bottom_span > .05).all() and (flap_span > .025).all()):
        raise ValueError("Observed layer spans are inconsistent")
    lower_width = TOWEL_SIZE[1] * bottom_span / (bottom_span + flap_span)
    crease += crease_shift_mm * .001
    lower_width = np.clip(lower_width + crease_shift_mm*.001, .08, .20)
    from_crease = original[:,1] + TOWEL_SIZE[1]/2 - lower_width
    upper = from_crease > 0.
    # The upper panel lies above the lower panel. The narrow folded rim is
    # rounded across the mesh rather than represented as a hard zero-radius hinge.
    y = crease - np.abs(from_crease)
    rim = np.clip(from_crease/thickness, -1., 1.)
    z = thickness*.75 + thickness*.53*(1.+rim)
    return np.column_stack((x, y, z)), upper


def scene(original, spacing, nx, ny, friction, thickness, mass, material, *, mounts=None, table=None, servo=False,
          model_name="FlexiFold field measured replay"):
    pads = []
    actuators = []
    for side in SIDES:
        fingers = []
        for half, direction in (("a", -1), ("b", 1)):
            name = f"{side}_pad_{half}"
            geom = f'<geom name="{name}" class="jaw_pad"/>'
            if servo:
                fingers.append(f'<body name="{name}" pos="0 {direction*.0025} 0">'
                               f'<joint name="{name}" type="slide" axis="0 {direction} 0" '
                               f'range="0 .035" damping=".025" armature=".003"/>{geom}</body>')
                actuators.append(f'<position name="{name}" joint="{name}" kp="18000" kv="18" '
                                 'ctrlrange="0 .035" forcerange="-40 40"/>')
            else:
                pads.append(f'<body name="{name}" mocap="true" pos="0 0 .9">{geom}</body>')
        if servo:
            pads.append(f'<body name="{side}_pad_wrist" mocap="true" pos="0 0 .9">{"".join(fingers)}</body>')
    bands = [(arm, x*ny+y) for arm, xs in enumerate((range(3), range(nx-3, nx)))
             for x in xs for y in range(ny)]
    anchors = [
        f'<body name="anchor_{arm}_{vertex}" pos="{orig[0]} {orig[1]} {orig[2]}" mocap="true"/>'
        for arm, vertex in bands for orig in [original[vertex]]]
    constraints = [
        f'<connect name="anchor_{arm}_{vertex}" body1="anchor_{arm}_{vertex}" '
        f'body2="towel_{vertex}" anchor="0 0 0" active="false" solref=".012 1"/>'
        for arm, vertex in bands]
    camera = ""
    if table is not None:
        basis = np.array(table["table_to_camera_rotation"])
        position = -np.asarray(table["table_origin_camera_m"]) @ basis
        xyaxes = np.concatenate((basis[0], -basis[1]))
        fovy = 2*np.rad2deg(np.arctan(240/table["color_intrinsics"]["fy"]))
        position_text = " ".join(map(str, position))
        axes_text = " ".join(map(str, xyaxes))
        camera = f'<camera name="field_rgb" pos="{position_text}" xyaxes="{axes_text}" fovy="{fovy}"/>'
    native_shell = material.discrete or material.bending_modulus_pa > 0
    integrator = "discrete" if native_shell else "implicitfast"
    edge_damping = "" if native_shell else 'damping=".12"'
    elasticity = ""
    if material.bending_modulus_pa > 0:
        elasticity = (f'<elasticity young="{material.bending_modulus_pa}" poisson=".30" '
                      f'thickness="{thickness}" damping="{material.damping_s}" elastic2d="bend"/>')
    xml = f"""
    <mujoco model="{model_name}">
      <option timestep="{DT}" integrator="{integrator}" iterations="35" tolerance="1e-7"
              solver="{'CG' if native_shell else 'Newton'}"/>
      <default><default class="jaw_pad">
        <geom type="box" size="{' '.join(map(str, PAD_HALF_SIZE))}" friction="1.0 .012 .001"
              mass=".025" rgba=".3 .3 .3 0"/>
      </default></default>
      <worldbody>
        <light pos="0 0 .9" dir="0 0 -1"/>
        <geom name="table" type="plane" size=".8 .55 .03" rgba=".70 .69 .64 1"
              friction="{friction} .012 .001"/>
        {camera}{''.join(pads)}{''.join(anchors)}
        <flexcomp name="towel" type="grid" dim="2" count="{nx} {ny} 1"
                  spacing="{spacing[0]} {spacing[1]} .02" pos="0 0 .005"
                  radius="{thickness/2}" mass="{mass}" rgba=".1 .37 .62 1">
          <contact selfcollide="auto" friction="{friction} .012 .001" solref=".012 1"/>
          <edge equality="true" solref=".016 1" {edge_damping}/>
          {elasticity}
        </flexcomp>
      </worldbody><equality>{''.join(constraints)}</equality><actuator>{''.join(actuators)}</actuator>
    </mujoco>"""
    if mounts is not None:
        xml = PiperDisplay.add_arms(xml, mounts)
        xml = xml.replace("FlexiFold dual PiPER", model_name)
    return mujoco.MjModel.from_xml_string(xml), bands


def footprint(vertices, model, table, origin_area):
    points = vertices @ np.asarray(table["table_to_camera_rotation"]).T + table["table_origin_camera_m"]
    pixels = project(points, table["color_intrinsics"])
    mask = np.zeros((480, 640), np.uint8)
    for tri in model.flex_elem.reshape(-1, 3):
        cv2.fillConvexPoly(mask, np.rint(pixels[tri]).astype(np.int32), 255)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return dict(area_ratio=0., mask_pixels=0, height_p90_mm=float(np.percentile(vertices[:, 2], 90)*1000))
    contour = max(contours, key=cv2.contourArea)
    return dict(area_ratio=float(cv2.contourArea(contour)/origin_area), mask_pixels=int(cv2.countNonZero(mask)),
                height_p90_mm=float(np.percentile(vertices[:, 2], 90)*1000),
                image_bbox=list(cv2.boundingRect(contour)))


def dihedral_pairs(model):
    shared_edges = {}
    for index, (a, b, c) in enumerate(model.flex_elem.reshape(-1, 3)):
        for u, v in ((a, b), (b, c), (c, a)):
            shared_edges.setdefault((min(u, v), max(u, v)), []).append(index)
    return np.array([incident for incident in shared_edges.values() if len(incident) == 2])


def surface_roughness(vertices, model, pairs):
    triangles = vertices[model.flex_elem.reshape(-1, 3)]
    normals = np.cross(triangles[:,1] - triangles[:,0], triangles[:,2] - triangles[:,0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1.e-10)
    cosines = np.clip(np.einsum("ij,ij->i", normals[pairs[:,0]], normals[pairs[:,1]]), -1., 1.)
    return float(np.percentile(np.rad2deg(np.arccos(cosines)), 75))


def second_material_alignment(vertices, nx, ny):
    grid = vertices.reshape(nx, ny, 3)
    half = nx//2
    left, right = grid[:half], grid[-half:][::-1]
    errors = np.linalg.norm(left[..., :2]-right[..., :2], axis=-1)*1000.
    return [float(error) for error in np.percentile(errors, [50,90])]


def compare_final_surface(vertices, model, table, recording):
    """Sample the simulated upper surface from the same color-camera perspective."""
    camera = vertices @ np.asarray(table["table_to_camera_rotation"]).T + table["table_origin_camera_m"]
    pixels = project(camera, table["color_intrinsics"])
    depth = np.full((480, 640), np.inf)
    height = np.full(depth.shape, np.nan)
    for triangle in model.flex_elem.reshape(-1, 3):
        coords = pixels[triangle]
        low = np.maximum(np.floor(coords.min(axis=0)).astype(int), [0, 0])
        high = np.minimum(np.ceil(coords.max(axis=0)).astype(int), [639, 479])
        bounds = high - low
        if np.any(bounds < 0):
            continue
        jacobian = (coords[1:] - coords[0]).T
        if abs(np.linalg.det(jacobian)) < 1.e-6:
            continue
        xs, ys = np.meshgrid(np.arange(low[0], high[0]+1), np.arange(low[1], high[1]+1))
        bary = (np.stack((xs, ys), axis=-1) - coords[0]) @ np.linalg.inv(jacobian).T
        weights = np.dstack((1.-bary.sum(axis=-1), bary))
        inv_depth = weights @ (1./camera[triangle, 2])
        values = 1./inv_depth
        visible = (weights >= -.005).all(axis=-1) & (values < depth[ys,xs])
        x, y = xs[visible], ys[visible]
        depth[y,x] = values[visible]
        height[y,x] = ((weights @ (vertices[triangle, 2]/camera[triangle, 2])) / inv_depth)[visible]
    valid = np.isfinite(height)
    interior = cv2.erode(valid.astype(np.uint8), np.ones((15, 15), np.uint8)).astype(bool)
    real = cv2.imread(str(recording / "last_middle.png"))
    feature = cloth_features(cv2.cvtColor(real, cv2.COLOR_BGR2RGB), SEGMENTATION)
    actual_mask = feature["mask"] > 0
    return dict(
        final_image_iou=float((actual_mask & valid).sum()/max(1, (actual_mask | valid).sum())),
        vertices_inside_camera_fraction=float(np.mean(
            (pixels[:,0] >= 0) & (pixels[:,0] < 640) & (pixels[:,1] >= 0) & (pixels[:,1] < 480))),
        visible_height_p50_mm=float(np.percentile(height[interior], 50)*1000.) if interior.any() else None,
        visible_height_p90_mm=float(np.percentile(height[interior], 90)*1000.) if interior.any() else None,
    )


class RecordedGrip:
    """Optional attachment driven by measured closure and pad proximity."""

    def __init__(self, model, bands, ny, mode, folded_upper=None):
        self.model = model
        self.mode = mode
        self.ny = ny
        self.opened = [False, False]
        self.loaded_since = [None, None]
        self.last_held = [False, False]
        self.active = [{}, {}]
        self.pinched_width_mm = [None, None]
        self.unload_since = [None, None]
        self.attempts = []
        self.releases = []
        self.grasps = [0, int(folded_upper is not None)]
        self.folded_upper = folded_upper
        self.index = []
        for arm in range(2):
            entries = {}
            for index, vertex in bands:
                if index == arm:
                    key = f"anchor_{arm}_{vertex}"
                    entries[vertex] = (model.body(key).mocapid[0], model.equality(key).id)
            self.index.append(entries)

    def update(self, data, step, now, jaw, rot, width, effort, last_command, threshold):
        if self.mode not in ("measured_grip", "aperture_grip", "layered_measured_grip"):
            return
        for i in range(2):
            if width[i] >= 24.:
                self.opened[i] = True
                self.last_held[i] = False
            loaded = (self.opened[i] and 1.5 <= width[i] <= 9.
                      and effort[i] <= -.18 and width[i] >= last_command[i] + .45)
            if not loaded:
                self.loaded_since[i] = None
            elif self.loaded_since[i] is None:
                self.loaded_since[i] = now
            if self.active[i]:
                if loaded:
                    self.pinched_width_mm[i] = min(self.pinched_width_mm[i], width[i])
                opened_again = (width[i] > self.pinched_width_mm[i] + .8
                                and abs(effort[i]) < .22)
                if opened_again and self.unload_since[i] is None:
                    self.unload_since[i] = now
                if not opened_again:
                    self.unload_since[i] = None
                unloaded = self.unload_since[i] is not None and now-self.unload_since[i] >= .07
                release = width[i] > 8. and abs(effort[i]) < .26 if self.mode == "aperture_grip" else unloaded
                if width[i] >= 24. or release:
                    for vertex in self.active[i]:
                        data.eq_active[self.index[i][vertex][1]] = False
                    self.active[i].clear()
                    self.releases.append(dict(step=step, arm=SIDES[i], width_mm=width[i],
                                              effort_Nm=effort[i], pinched_width_mm=self.pinched_width_mm[i]))
                    self.opened[i] = width[i] >= 24.
                    self.unload_since[i] = None
            if (self.loaded_since[i] is not None and now-self.loaded_since[i] >= .10
                    and not self.last_held[i]):
                self.last_held[i] = True
                indices = list(self.index[i])
                positions = data.flexvert_xpos[indices].copy()
                distances = np.linalg.norm(positions - jaw[i], axis=1)
                nearest = int(distances.argmin())
                captured = float(distances[nearest]) <= threshold
                event = dict(step=step, arm=SIDES[i], distance_mm=float(distances[nearest]*1000),
                             captured=captured, attached_points=0)
                self.attempts.append(event)
                if captured:
                    vertex = indices[nearest]
                    self.active[i][vertex] = (positions[nearest] - jaw[i]) @ rot[i]
                    if self.mode == "layered_measured_grip" and i == 1 and self.grasps[i] == 1:
                        opposite = self.other_layer(vertex, indices, positions)
                        if opposite is not None:
                            other = data.flexvert_xpos[opposite].copy()
                            separation = float(np.linalg.norm(other - positions[nearest]))
                            event["paired_material_distance_mm"] = separation*1000.
                            if separation <= .018 and np.linalg.norm(other - jaw[i]) <= threshold:
                                self.active[i][opposite] = (other - jaw[i]) @ rot[i]
                    self.grasps[i] += 1
                    self.pinched_width_mm[i] = width[i]
                    event["attached_points"] = len(self.active[i])
                    for change in self.active[i]:
                        data.eq_active[self.index[i][change][1]] = True
            for vertex, offset in self.active[i].items():
                data.mocap_pos[self.index[i][vertex][0]] = jaw[i] + rot[i] @ offset

    def other_layer(self, vertex, indices, positions):
        if self.folded_upper is None:
            mirrored = (vertex // self.ny)*self.ny + self.ny - 1 - vertex % self.ny
            return mirrored if mirrored != vertex and mirrored in self.index[1] else None
        opposite = self.folded_upper[indices] != self.folded_upper[vertex]
        eligible = np.flatnonzero(opposite)
        if eligible.size == 0:
            return None
        delta = positions[eligible] - positions[indices.index(vertex)]
        return indices[eligible[np.argmin(np.linalg.norm(delta, axis=1))]]


class ReplayWindow:
    def __init__(self, model, data, speed):
        import mujoco.viewer as window
        self.model, self.data = model, data
        self.speed = speed
        self.replay_key = threading.Event()
        self.camera_key = threading.Event()
        self.frames = []
        self.handle = window.launch_passive(model, data, key_callback=self.key,
                                            show_left_ui=False, show_right_ui=False)
        self.update_camera(False)
        print("FIELD_MUJOCO_WINDOW_OPEN", flush=True)

    def key(self, key):
        if key == ord("R"):
            self.replay_key.set()
        if key == ord("C"):
            self.camera_key.set()

    def update_camera(self, fixed):
        with self.handle.lock():
            cam = self.handle.cam
            cam.type = mujoco.mjtCamera.mjCAMERA_FIXED if fixed else mujoco.mjtCamera.mjCAMERA_FREE
            if fixed:
                cam.fixedcamid = self.model.camera("field_rgb").id
            else:
                cam.lookat[:] = (.045, -.10, .11)
                cam.azimuth = 37.
                cam.elevation = -39.
                cam.distance = 1.31

    def draw(self, step, phase, gaps):
        if self.camera_key.is_set():
            self.camera_key.clear()
            self.update_camera(self.handle.cam.type != mujoco.mjtCamera.mjCAMERA_FIXED)
        with self.handle.lock():
            scene = self.handle.user_scn
            scene.ngeom = 1
            label = scene.geoms[0]
            mujoco.mjv_initGeom(label, mujoco.mjtGeom.mjGEOM_LABEL, (0, 0, 0),
                               (-.04, -.12, .018), np.eye(3).ravel(), (.96, .97, 1., 1.))
            label.label = f"{phase}  |  Step {step}  |  L {gaps[0]:.1f} R {gaps[1]:.1f} mm"
        self.handle.sync()

    def record(self, label, step, gaps, elapsed):
        state_spec = mujoco.mjtState.mjSTATE_INTEGRATION
        state = np.empty(mujoco.mj_stateSize(self.model, state_spec))
        mujoco.mj_getState(self.model, self.data, state, state_spec)
        self.frames.append((state, step, label, gaps.copy(), elapsed))
        self.draw(step, label, gaps)
        time.sleep(1/32)

    def hold(self):
        state_spec = mujoco.mjtState.mjSTATE_INTEGRATION
        try:
            last = self.frames[-1]
            while self.handle.is_running():
                if self.replay_key.wait(.1):
                    self.replay_key.clear()
                    previous = self.frames[0][-1]
                    for state, step, phase, gaps, elapsed in self.frames:
                        if not self.handle.is_running():
                            return
                        time.sleep((elapsed-previous)/self.speed)
                        mujoco.mj_setState(self.model, self.data, state, state_spec)
                        mujoco.mj_forward(self.model, self.data)
                        self.draw(step, phase, gaps)
                        previous = elapsed
                else:
                    self.draw(last[1], "R: replay | C: field camera", last[3])
        finally:
            self.handle.close()


def replay(root, run, *, mode, nx=17, ny=11, friction=.75, thickness=.0035, mass=.085,
           max_capture_mm=45., view=False, speed=1.8, pad_offset_mm=None, on_frame=None,
           material=Material(), folded_observation=None, crease_shift_mm=0., bound_pad_to_table=False):
    states = read_json(run.trace)
    rows = states["rows"]
    if folded_observation is not None:
        start = folded_observation["step"]
        rows = [row for row in rows if row["step"] >= start]
    geometry = read_json(root / "table_geometry.json")
    table = geometry["table"]
    mounts = read_json(root / "estimated_mounts.json")
    outline = next(item["initial"] for key, item in geometry["recordings"].items()
                   if Path(key).name == run.recording.name)
    original, spacing, center, yaw = initial_grid(outline, nx, ny)
    measured, jaw_positions, rotations = frame_kinematics(rows, mounts)
    # Recorded jaw distance is the total finger-to-finger gap.
    feedback = np.array([[(row["gripper_feedback_before_action"][side]["width_mm"],
                          row["gripper_feedback_before_action"][side]["effort_Nm"]) for side in SIDES]
                         for row in rows])
    times = np.array([row["tick_monotonic_s"] for row in rows])
    times -= times[0]
    servo = mode == "servo_contact"
    if bound_pad_to_table and not servo:
        raise ValueError("The table bound is only available with servo_contact")
    render = view or on_frame is not None
    model_name = "FlexiFold observed second fold" if folded_observation is not None else "FlexiFold field measured replay"
    model, bands = scene(original, spacing, nx, ny, friction, thickness, mass, material,
                         mounts=mounts if render else None, table=table if render else None, servo=servo,
                         model_name=model_name)
    data = mujoco.MjData(model)
    folded_upper = None
    if folded_observation is None:
        lay = np.column_stack((observed_lay(original, outline, center, yaw), original[:,2]))
    else:
        lay, folded_upper = place_observed_first_fold(
            original, center, folded_observation, thickness, crease_shift_mm)
    for i, (vertex, placed) in enumerate(zip(original, lay, strict=True)):
        body = model.body(f"towel_{i}")
        start = model.jnt_qposadr[body.jntadr[0]]
        data.qpos[start:start+3] = placed - vertex
    pad_names = [[f"{side}_pad_{shape}" for shape in ("a", "b")] for side in SIDES]
    if servo:
        pad_ids = np.array([model.body(f"{side}_pad_wrist").mocapid[0] for side in SIDES])
        actuator_ids = np.array([[model.actuator(name).id for name in pair] for pair in pad_names])
        finger_indices = np.array([[model.joint(name).qposadr[0] for name in pair] for pair in pad_names])
        first_opening = np.clip(.0005*feedback[0,:,0], 0., .035)
        data.qpos[finger_indices] = first_opening[:, None]
        data.ctrl[actuator_ids] = first_opening[:, None]
    else:
        pad_ids = np.array([[model.body(name).mocapid[0] for name in pair] for pair in pad_names])
    geom_ids = {model.geom(name).id for pair in pad_names for name in pair}
    grip = RecordedGrip(model, bands, ny, mode, folded_upper)
    view_ids = None
    viewer = None
    if render:
        view_ids = np.array([[model.joint(f"{side}_joint{i}").qposadr[0] for i in range(1, 9)] for side in SIDES])
    if view:
        viewer = ReplayWindow(model, data, speed)
    mujoco.mj_forward(model, data)
    for _ in range(round(.25/DT)):
        mujoco.mj_step(model, data)
    initial = footprint(data.flexvert_xpos, model, table, 1.)
    origin_area = initial["area_ratio"] if folded_observation is None else outline["area_px"]
    triangle_pairs = dihedral_pairs(model)

    def measure():
        values = footprint(data.flexvert_xpos, model, table, origin_area)
        values["surface_dihedral_p75_deg"] = surface_roughness(data.flexvert_xpos, model, triangle_pairs)
        return values

    key_steps = {run.first_eval_step: "after_first", run.second_eval_step: "after_second"}
    phases = {}
    matrix = np.empty((2, 9))
    sample = 0
    display_period = (1/32) * speed
    next_view = 0.
    minimum_z = np.full(2, np.inf)
    pad_offset = np.zeros((2, 3)) if pad_offset_mm is None else np.asarray(pad_offset_mm)*.001
    contact_ticks = 0
    peak_pad_force = 0.
    while sample*DT <= times[-1]:
        elapsed = sample * DT
        j = min(len(times)-2, max(0, bisect_left(times, elapsed)-1))
        mix = np.clip((elapsed-times[j]) / (times[j+1]-times[j]), 0., 1.)
        jaw = (1.-mix)*jaw_positions[j] + mix*jaw_positions[j+1]
        trace_grips = (1.-mix)*feedback[j] + mix*feedback[j+1]
        width = np.clip(trace_grips[:, 0], 0, 70.)
        if view_ids is not None:
            pose = (1.-mix)*measured[j] + mix*measured[j+1]
            for k in range(2):
                data.qpos[view_ids[k,:6]] = pose[7*k:7*k+6]
                data.qpos[view_ids[k,6:]] = np.array((.5, -.5)) * .001*width[k]
            # Joint positions are prescribed by feedback. The separate pads
            # drive cloth contacts in either the displayed or headless model.
        quats = (1.-mix)*rotations[j] + mix*rotations[j+1]
        quats /= np.linalg.norm(quats, axis=1, keepdims=True)
        for arm in range(2):
            mujoco.mju_quat2Mat(matrix[arm], quats[arm])
        rot = matrix.reshape(2, 3, 3)
        normal = rot[:, :, 1]
        # Pads extend inward along their closing axis and back from the
        # visually registered end of the two fingers.
        centers = jaw - .011*rot[:, :, 2] + np.einsum("ijk,ik->ij", rot, pad_offset)
        if bound_pad_to_table:
            half_opening = np.clip(data.qpos[finger_indices].max(axis=1), 0., .035)
            # Lift the pair together so the rigid pads cannot cut through the table.
            extent = np.abs(rot[:, 2, :]) @ PAD_HALF_SIZE
            lowest = centers[:, 2] - extent - (.0025 + half_opening)*np.abs(normal[:, 2])
            centers[:, 2] -= np.minimum(lowest, 0.)
        row = rows[j]
        if servo:
            data.mocap_pos[pad_ids] = centers
            data.mocap_quat[pad_ids] = quats
            openings = np.clip(np.array(row["target"])[[6, 13]]*.5, 0., .035)
            data.ctrl[actuator_ids] = openings[:, None]
        else:
            delta = (.0005*width + .0025)[:, None] * normal
            data.mocap_pos[pad_ids] = np.stack((centers-delta, centers+delta), axis=1)
            data.mocap_quat[pad_ids] = quats[:, None, :]
        minimum_z = np.minimum(minimum_z, jaw[:,2])
        command = np.array(row["previous"])[[6,13]]*1000.
        grip.update(data, row["step"], elapsed, jaw, rot, width, trace_grips[:,1],
                    command, max_capture_mm/1000.)
        mujoco.mj_step(model, data)
        if sample % 10 == 0:
            if any((contact.flex >= 0).any() and any(geom in geom_ids for geom in contact.geom)
                   for contact in data.contact):
                contact_ticks += 1
            if servo:
                peak_pad_force = max(peak_pad_force, float(np.max(np.abs(data.actuator_force))))
        for step, label in key_steps.items():
            if row["step"] >= step and label not in phases:
                phases[label] = measure()
        if viewer is not None and elapsed >= next_view and viewer.handle.is_running():
            source = "Recorded trajectory, simulated contact" if servo else "Recorded trajectory and grip"
            viewer.record(source, row["step"], width, elapsed)
            next_view += display_period
        if on_frame is not None:
            on_frame(model, data, row["step"], elapsed)
        sample += 1
    phases["final"] = measure()
    phases["final"].update(compare_final_surface(data.flexvert_xpos, model, table, run.recording))
    phases["final"]["second_material_alignment_p50_p90_mm"] = second_material_alignment(
        data.flexvert_xpos, nx, ny)
    stiffness = material.bending_modulus_pa * thickness**3 / (12 * (1. - .30**2))
    result = dict(physical_outcome=run.label, mode=mode, mesh=[nx, ny], friction=friction,
                  thickness_mm=thickness*1000, assumed_mass_g=mass*1000, towel_size_mm=(TOWEL_SIZE*1000).tolist(),
                  bending_rigidity_Nm=stiffness, bending_damping_s=material.damping_s,
                  simulation_seconds=float(times[-1]), phases=phases, recorded_load_attachments=grip.attempts,
                  recorded_load_releases=grip.releases,
                  pad_to_cloth_contact_samples=contact_ticks, max_pad_actuator_force_n=peak_pad_force if servo else None,
                  minimum_jaw_z_table_mm=(1000*minimum_z).tolist(), finish_check_from_robot=states.get("finish_check"),
                  initialized_from_saved_fold=folded_observation is not None,
                  crease_shift_mm=crease_shift_mm, bounded_pad_to_table=bound_pad_to_table, robot_commands_sent=False)
    return result, viewer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mode", choices=("contact_only", "servo_contact", "measured_grip", "aperture_grip",
                                           "layered_measured_grip"),
                        default="servo_contact")
    parser.add_argument("--view", action="store_true")
    parser.add_argument("--speed", type=float, default=1.8)
    parser.add_argument("--bending-modulus-pa", type=float, default=0.)
    parser.add_argument("--bending-damping-s", type=float, default=0.)
    parser.add_argument("--from-observed-first-fold", action="store_true")
    parser.add_argument("--bound-pad-to-table", action="store_true",
                        help="Limit estimated pad penetration on servo_contact replays")
    args = parser.parse_args()
    if not np.isfinite(args.speed) or args.speed <= 0:
        parser.error("--speed must be positive and finite")
    if args.bound_pad_to_table and args.mode != "servo_contact":
        parser.error("--bound-pad-to-table requires servo_contact")
    row = run_from_manifest(args.root, args.run)
    material = Material(args.bending_modulus_pa, args.bending_damping_s)
    observed = None
    if args.from_observed_first_fold:
        entry = read_json(args.root/"observed_folded_layers.json")[args.run]
        if not entry["fitted_from_video"]:
            parser.error("No stable first-fold observation was found for this recording")
        observed = entry["reference_frame"]
    result, viewer = replay(args.root, row, mode=args.mode, view=args.view, speed=args.speed,
                            material=material, folded_observation=observed,
                            bound_pad_to_table=args.bound_pad_to_table)
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result, allow_nan=False), flush=True)
    if viewer is not None:
        viewer.hold()


if __name__ == "__main__":
    main()
