"""Attach two Menagerie PiPER models to the cloth cycle for kinematic display."""

from pathlib import Path
import xml.etree.ElementTree as ET

import mujoco
import numpy as np

from simulate_towel_robustness import scene


ASSETS = Path(__file__).resolve().parent / "assets" / "agilex_piper"
LINKS = ("base_link", *(f"link{i}" for i in range(1, 9)))
BASES = np.array(((-.223, -.335, 0), (.223, -.335, 0)))
SIDES = ("left", "right")
STROKE_M = .070
CLOSED_M = .004
MAX_SLEW_M_S = .18
TWO_FINGER_SPECS = dict(stroke_mm=70, nominal_force_n=40, maximum_force_n=50, mass_kg=.50)
JOINT_MAX_SPEED_RAD_S = np.deg2rad((180., 195., 180., 225., 225., 225.))


class PiperDisplay:
    """Fit real-sized arm and finger geometry to the simulated grasp points."""

    def __init__(self):
        self.model = None
        self.q = np.repeat(np.array([[0., 1.05, -.59, 0., 1.16, 0.]]), 2, axis=0)
        self.widths = np.full(2, .006)
        self.errors = []
        self.gaps = []
        self.last_time = None

    def build(self, friction, mass):
        model = scene(friction, mass, augment=self.add_arms)
        self.model = model
        self.indices = []
        for side in SIDES:
            ids = np.array([model.joint(f"{side}_joint{i}").id for i in range(1, 9)])
            self.indices.append((model.jnt_qposadr[ids], model.jnt_dofadr[ids], model.jnt_range[ids],
                                 model.site(f"{side}_jaw_center").id))
        self.position_jac = np.zeros((3, model.nv))
        self.rotation_jac = np.zeros_like(self.position_jac)
        self.build_seeds()
        return model

    def build_seeds(self):
        """Sample the arm's vertical plane to initialize bounded inverse kinematics."""
        model = self.model
        data = mujoco.MjData(model)
        indices, _, limits, site = self.indices[0]
        spans = [np.linspace(*limits[i], count) for i, count in ((1, 25), (2, 25), (4, 19))]
        angle_grid = np.stack(np.meshgrid(*spans, indexing="ij"), axis=-1).reshape(-1, 3)
        self.seed_angles = angle_grid
        self.seed_sites = np.empty((len(angle_grid), 4))
        for i, angles in enumerate(angle_grid):
            data.qpos[indices[[1, 2, 4]]] = angles
            mujoco.mj_kinematics(model, data)
            self.seed_sites[i, :3] = data.site_xpos[site] - BASES[0]
            self.seed_sites[i, 3] = data.site_xmat[site, 8]

    @staticmethod
    def add_arms(xml, mounts=None):
        root = ET.fromstring(xml)
        root.set("model", "FlexiFold dual PiPER")
        ET.SubElement(root, "compiler", angle="radian")
        asset = ET.SubElement(root, "asset")
        for name in LINKS:
            ET.SubElement(asset, "mesh", name=name, file=str(ASSETS / "assets" / f"{name}.stl"))
        world = root.find("worldbody")
        placements = [(base, 90.) for base in BASES]
        if mounts is not None:
            placements = [(mounts[side]["base_xyz_table_m"], mounts[side]["yaw_deg"]) for side in SIDES]
        for side, (base, yaw), tint in zip(SIDES, placements, (".20 .58 .67 1", ".86 .47 .21 1"), strict=True):
            arm = ET.parse(ASSETS / "piper.xml").find("worldbody/body")
            arm.set("pos", " ".join(map(str, base)))
            half_yaw = np.deg2rad(yaw) / 2
            arm.set("quat", f"{np.cos(half_yaw)} 0 0 {np.sin(half_yaw)}")
            for body in arm.iter("body"):
                name = body.attrib["name"]
                body.set("name", f"{side}_{name}")
                body.attrib.pop("childclass", None)
                geometry = next(geom for geom in body.findall("geom") if geom.get("mesh") == name)
                for geom in list(body.findall("geom")):
                    if geom is not geometry:
                        body.remove(geom)
                geometry.attrib.pop("class", None)
                geometry.attrib.pop("material", None)
                geometry.attrib.update(type="mesh", contype="0", conaffinity="0", density="0")
                geometry.set("rgba", tint if name in ("base_link", "link2") else ".58 .61 .64 1")
                joint = body.find("joint")
                if joint is not None:
                    joint.set("name", f"{side}_{joint.attrib['name']}")
                    joint.attrib.pop("class", None)
                    if name in ("link7", "link8"):
                        joint.set("type", "slide")
                        joint.set("range", f"0 {STROKE_M/2}" if name == "link7" else f"{-STROKE_M/2} 0")
                        geometry.set("rgba", ".14 .16 .18 1")
                    if name == "link6":
                        limit = np.deg2rad(100.)
                        joint.set("range", f"{-limit} {limit}")
                if name == "link6":
                    ET.SubElement(body, "site", name=f"{side}_jaw_center", pos="0 0 .133",
                                  size=".001", rgba="0 0 0 0")
            world.append(arm)
        return ET.tostring(root, encoding="unicode")

    def track(self, data, stage, targets, closed):
        first_frame = self.last_time is None
        elapsed = .03 if first_frame else max(.01, data.time - self.last_time)
        self.last_time = data.time
        for i, (q_indices, dofs, limits, site) in enumerate(self.indices):
            wanted = CLOSED_M if closed[i] else self.opening(stage, i)
            change = np.clip(wanted - self.widths[i], -MAX_SLEW_M_S * elapsed, MAX_SLEW_M_S * elapsed)
            self.widths[i] = np.clip(self.widths[i] + change, 0., STROKE_M)
            data.qpos[q_indices[-2:]] = np.array([.5, -.5]) * self.widths[i]
            data.qvel[dofs] = 0.
            angles, error = self.fit(data, self.q[i], q_indices[:6], dofs[:6],
                                     limits[:6], site, targets[i])
            if error > 1.2:
                seed = self.seed(i, targets[i], self.q[i])
                alternative, alternative_error = self.fit(data, seed, q_indices[:6], dofs[:6],
                                                           limits[:6], site, targets[i])
                if alternative_error < error:
                    angles, error = alternative, alternative_error
            self.q[i] = angles
            data.qpos[q_indices[:6]] = angles
            self.errors.append((stage, i, error))
        mujoco.mj_kinematics(self.model, data)
        self.gaps.append(self.widths.copy())

    @staticmethod
    def opening(stage, arm):
        if stage in ("Ready", "Cycle complete", "Retreat", "Return home"):
            return .006
        if stage == "First approach":
            return .055
        if stage.startswith("Second"):
            return .058 if arm == 1 else .006
        return .045

    def seed(self, arm, goal, previous):
        local = goal - BASES[arm]
        distance = np.hypot(*local[:2])
        positions = self.seed_sites
        radial_error = np.hypot(positions[:, 0], positions[:, 1]) - distance
        score = np.hypot(radial_error, positions[:, 2] - local[2])
        score += .012 * (positions[:, 3] + 1.)
        score += .003 * np.linalg.norm(self.seed_angles - previous[[1, 2, 4]], axis=1)
        score[positions[:, 1] < 0.] = np.inf
        best = int(score.argmin())
        yaw = np.arctan2(positions[best, 0], positions[best, 1]) - np.arctan2(local[0], local[1])
        j2, j3, j5 = self.seed_angles[best]
        return np.array((yaw, j2, j3, 0., j5, previous[5]))

    def fit(self, data, start, indices, dofs, limits, site, target):
        model = self.model
        q = np.clip(start, limits[:, 0], limits[:, 1])
        down = np.array((0., 0., -1.))

        def pose(q):
            data.qpos[indices] = q
            mujoco.mj_kinematics(model, data)
            position_error = target - data.site_xpos[site]
            axis = data.site_xmat[site].reshape(3, 3)[:, 2]
            return position_error, axis

        # Prefer a downward tool axis, then refine position independently.
        # A bounded line search keeps the solution close to the last frame.
        position_error, axis = pose(q)
        for orient in (.009, 0.):
            for _ in range(12):
                if np.linalg.norm(position_error) < .00025:
                    break
                mujoco.mj_comPos(model, data)
                mujoco.mj_jacSite(model, data, self.position_jac, self.rotation_jac, site)
                J = self.position_jac[:, dofs]
                axis_jac = np.cross(self.rotation_jac[:, dofs].T, axis).T
                J = np.vstack((J, orient * axis_jac))
                residual = np.r_[position_error, orient * (down - axis)]
                score = residual @ residual
                grad = J.T @ residual
                free = ((q > limits[:, 0] + 1e-8) | (grad > 0.)) & ((q < limits[:, 1] - 1e-8) | (grad < 0.))
                J_free = J[:, free]
                step = np.zeros(6)
                step[free] = np.linalg.solve(J_free.T @ J_free + 1e-5*np.eye(free.sum()), grad[free])
                for scale in (1., .5, .25, .125):
                    candidate = np.clip(q + scale * np.clip(step, -.17, .17), limits[:, 0], limits[:, 1])
                    error, candidate_axis = pose(candidate)
                    if error @ error + orient**2 * np.sum((down - candidate_axis)**2) < score:
                        q, position_error, axis = candidate, error, candidate_axis
                        break
                else:
                    position_error, axis = pose(q)
                    break
        return q, float(1000. * np.linalg.norm(position_error))

    def report(self):
        errors = np.array([error for _, _, error in self.errors])
        return dict(model="MuJoCo Menagerie AgileX PiPER", mounted_base_m=BASES.tolist(),
                    two_finger_specs=TWO_FINGER_SPECS, geometry_contact="disabled",
                    playback_joint_max_speed_deg_s=np.rad2deg(JOINT_MAX_SPEED_RAD_S).round().tolist(),
                    fitted_frames=len(errors)//2,
                    jaw_registration_error_mm=dict(p95=float(np.percentile(errors, 95)), max=float(errors.max())),
                    observed_max_gap_mm=(1000 * np.max(self.gaps, axis=0)).tolist())
