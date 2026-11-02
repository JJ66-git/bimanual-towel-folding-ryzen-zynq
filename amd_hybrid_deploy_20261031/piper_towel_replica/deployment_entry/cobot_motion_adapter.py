"""Site envelope and official ACT configuration; no CAN or custom action queue.

The reference blend_core is copied unchanged. Its shared LOWER/UPPER arrays
are configured before inference, so candidate and final validators agree.
"""
import json
import math
from pathlib import Path

import numpy as np


def load_motion_profile(path, core):
    profile = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(profile, dict) or profile.get("schema") != "cobot_reference_motion_v1":
        raise ValueError("Unsupported Cobot motion profile")
    if profile.get("action_names") != core.ACTION_NAMES:
        raise ValueError("Motion profile must match reference 14D action order")
    lower = np.asarray(profile.get("software_lower"), dtype=np.float64)
    upper = np.asarray(profile.get("software_upper"), dtype=np.float64)
    if lower.shape != (14,) or upper.shape != (14,) or not np.isfinite([lower, upper]).all():
        raise ValueError("Motion envelope requires 14 finite lower/upper values")
    if np.any(lower >= upper):
        raise ValueError("Motion envelope has empty bounds")
    original_lower = np.tile(core.SINGLE_LOWER.astype(np.float64), 2)
    original_upper = np.tile(core.SINGLE_UPPER.astype(np.float64), 2)
    # This adaptation is scoped to the two conflicts demonstrated by the bundle.
    unchanged_lower = [i for i in range(14) if i not in (2, 9)]
    unchanged_upper = [i for i in range(14) if i not in (1, 8)]
    if (not np.allclose(lower[unchanged_lower], original_lower[unchanged_lower], atol=1e-7, rtol=0)
            or not np.allclose(upper[unchanged_upper], original_upper[unchanged_upper], atol=1e-7, rtol=0)):
        raise ValueError("Only demonstrated J2 upper / J3 lower adaptation is supported")
    evidence = profile["expansion_evidence"]
    if (evidence.get("sdk_query_reported_by_operator") is not True
            or evidence.get("j2_upper_deg") != 180.0 or evidence.get("j3_lower_deg") != -170.0):
        raise ValueError("J2/J3 expansion requires the recorded Cobot SDK query evidence")
    if (np.any(upper[[1, 8]] > math.pi) or np.any(lower[[2, 9]] < math.radians(-170))
            or np.any(upper[[1, 8]] < original_upper[[1, 8]])
            or np.any(lower[[2, 9]] > original_lower[[2, 9]])):
        raise ValueError("J2/J3 expansion conflicts with recorded hardware limits")
    max_j2 = np.asarray(evidence["dataset_j2_max_rad"], dtype=np.float64)
    min_j3 = np.asarray(evidence["dataset_j3_min_rad"], dtype=np.float64)
    margin = float(evidence["margin_rad"])
    if (max_j2.shape != (2,) or min_j3.shape != (2,) or margin != 0.02
            or not np.isfinite([max_j2, min_j3]).all()
            or np.any(upper[[1, 8]] < max_j2 + margin)
            or np.any(lower[[2, 9]] > min_j3 - margin)):
        raise ValueError("Envelope does not cover the reviewed demonstrations and reference margin")
    # Enforce the documented derivation, not a broad expansion to hardware stops.
    if (np.any(upper[[1, 8]] > math.ceil((max_j2.max()+margin)*100)/100 + 1e-7)
            or np.any(lower[[2, 9]] < math.floor((min_j3.min()-margin)*100)/100 - 1e-7)):
        raise ValueError("Envelope exceeds the documented task-specific expansion")
    inference = profile["act_inference"]
    if (inference.get("mode") != "saved_checkpoint"
            or "n_action_steps" in inference or "temporal_ensemble_coeff" in inference):
        raise ValueError("Use saved ACT checkpoint configuration without runtime overrides")
    return profile


def configure_bounds(profile, core):
    # In-place update is important: hy.py has already imported these same arrays.
    core.LOWER[:] = profile["software_lower"]
    core.UPPER[:] = profile["software_upper"]
    print("COBOT_MOTION_PROFILE=" + json.dumps(dict(
        software_lower=core.LOWER.tolist(), software_upper=core.UPPER.tolist(),
        act_inference=profile["act_inference"], native_sdk_limits_unchanged=True)), flush=True)


def configure_act(config, profile):
    """Validate the native cached path; do not mutate any checkpoint configuration."""
    if profile["act_inference"]["mode"] != "saved_checkpoint":
        raise ValueError("Unsupported ACT inference mode")
    if not 1 <= config.n_action_steps <= config.chunk_size:
        raise ValueError("Invalid saved ACT action horizon")
    if config.temporal_ensemble_coeff is not None:
        raise ValueError("Selected baseline requires a saved ACT checkpoint without temporal ensemble")
    return config


def constrain_correction(act_action, correction, previous_correction, args, core):
    """Keep the reference correction inside box, magnitude and slew constraints.

    ACT is never clipped here. Invalid ACT is rejected, not disguised as a
    correction. If the constraints have no common solution, stop explicitly.
    The final EMA remains a convex combination of legal positions when the
    previous target is legal; the reference final validator still runs.
    """
    values = [np.asarray(x, dtype=np.float64) for x in (act_action, correction, previous_correction)]
    if any(x.shape != (14,) or not np.isfinite(x).all() for x in values):
        raise ValueError("Expected finite 14D ACT/correction/previous correction")
    act, proposed, old = values
    core.validate_absolute(act)
    step = float(args.correction_step_limit)
    magnitude = float(args.smol_correction_limit)
    if not math.isfinite(step) or not math.isfinite(magnitude) or min(step, magnitude) <= 0:
        raise ValueError("Correction bounds must be finite and positive")
    arm = core.ARM
    lower = np.maximum.reduce([core.LOWER[arm].astype(float)-act[arm],
                               old[arm]-step, np.full(12, -magnitude)])
    upper = np.minimum.reduce([core.UPPER[arm].astype(float)-act[arm],
                               old[arm]+step, np.full(12, magnitude)])
    # Use the absolute validator's existing 1e-6 tolerance (also below the
    # reference correction-slew validator's 1e-5), not a new motion margin.
    # Float32 quantization can make an otherwise touching interval
    # appear empty by ~1e-7; do not misclassify that as infeasible motion.
    bad = np.flatnonzero(lower > upper + 1e-6)
    if bad.size:
        name = core.ACTION_NAMES[int(arm[int(bad[0])])]
        raise RuntimeError("No feasible bounded blend correction for "+name+
                           "; cannot preserve task envelope and reference correction slew")
    lower = np.minimum(lower, upper)
    result = np.asarray(correction, dtype=np.float32).copy()
    result[arm] = np.clip(proposed[arm], lower, upper)
    result[core.GRIPPER] = 0.0  # ACT keeps exclusive gripper ownership.
    return result
