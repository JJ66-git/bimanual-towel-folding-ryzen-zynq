#!/usr/bin/env python3
"""Dual-Piper towel folding: trusted guard base + continuously blended SmolVLA.

This file is independent of the existing ACT deployment.  It loads the proven
ACT checkpoint as the trusted base controller and lets SmolVLA shape the arm
trajectory through a *continuous authority blend*:

    target_arm = act_arm + authority * (smol_ema_arm - act_arm)

authority in [0, max_authority] rises while SmolVLA stays coherent with ACT and
decays whenever it is rejected, out of bounds, or the RTC queue is empty.
There is no ACT<->SMOL binary switch, so no per-tick command jump at handoff.

Gripper is always owned by the proven ACT controller.  The final blended
command is absolutely limited and the SmolVLA correction is bounded in both
magnitude and per-step slew, so it can never push the arm out of range or
cause a discontinuity.

The blend math lives in ``blend_core`` (pure, importable, unit-tested offline);
this module only wires it to the real ACT + SmolVLA inference engines.

Safety model (kept strict, no relaxation of the proven ACT semantics):
  - pure ACT ticks  -> trusted guard limits (joint step 1.0 / tracking 3.0), as before
  - blended ticks   -> validate_absolute(target) + ACT limits + bounded/slewed correction
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

import numpy as np
import torch

from lerobot.cameras.opencv import OpenCVCameraConfig
from lerobot.configs.types import RTCAttentionSchedule
from lerobot.policies import make_pre_post_processors
from lerobot.policies.act.modeling_act import ACTPolicy
from lerobot.policies.rtc.configuration_rtc import RTCConfig
from lerobot.policies.smolvla import SmolVLAPolicy
from lerobot.robots.piper_dual.config_piper_dual import PIPERDualConfig
from lerobot.robots.piper_dual.piper_dual import PIPERDual
from cobot_connection import connect_holding_measured, gripper_snapshot
from lerobot.rollout.inference.rtc import RTCInferenceEngine
from lerobot.rollout.inference.sync import SyncInferenceEngine
from lerobot.rollout.robot_wrapper import ThreadSafeRobot
from lerobot.utils.feature_utils import build_dataset_frame, hw_to_dataset_features

from blend_core import (
    ACTION_NAMES,
    ARM,
    GRIPPER,
    LOWER,
    UPPER,
    apply_global_smoothing,
    blend_step,
    max_parts,
    positions,
    validate_absolute,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared_runtime.dashboard_telemetry import create_run

SOFT_LIMIT_PROJECTION_MARGIN = 0.02


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--backend", choices=["cpu", "rocm"], default="cpu")
    p.add_argument("--precision", choices=["auto", "original", "float32"], default="auto")
    p.add_argument("--act-model", type=Path, required=True)
    p.add_argument("--smol-model", type=Path, required=True)
    p.add_argument("--left-index", type=int, required=True)
    p.add_argument("--middle-index", type=int, required=True)
    p.add_argument("--right-index", type=int, required=True)
    p.add_argument("--left-can", default="can1")
    p.add_argument("--right-can", default="can0")
    p.add_argument("--task", default="Fold the towel with both Piper arms.")
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument("--max-actions", type=int, default=750)
    p.add_argument("--handoff-step", type=int, default=300)
    p.add_argument("--handoff-file", type=Path, default=Path("/tmp/piper_hybrid_handoff"))
    p.add_argument("--guidance-weight", type=float, default=2.5)
    p.add_argument("--execution-horizon", type=int, default=10)
    p.add_argument("--queue-threshold", type=int, default=30)
    p.add_argument("--seed", type=int, default=42)

    # SmolVLA shaping / trust gates.
    p.add_argument(
        "--smol-lowpass-alpha",
        type=float,
        default=0.25,
        help="EMA weight of the raw SmolVLA action toward the current command.",
    )
    p.add_argument("--smol-raw-step-limit", type=float, default=0.60)
    p.add_argument("--smol-raw-policy-disagreement", type=float, default=0.50)
    p.add_argument(
        "--policy-disagreement",
        type=float,
        default=0.14,
        help="Soft trust gate: SmolVLA must stay this close to ACT to grow authority.",
    )
    p.add_argument(
        "--joint-step-limit",
        type=float,
        default=0.10,
        help="Soft trust gate: filtered SmolVLA per-step joint increment bound.",
    )

    # Authority dynamics.
    p.add_argument(
        "--max-authority",
        type=float,
        default=0.9,
        help="Upper bound of the continuous SmolVLA authority weight.",
    )
    p.add_argument(
        "--authority-up-rate",
        type=float,
        default=0.15,
        help="Exponential approach rate of authority toward max per accepted step.",
    )
    p.add_argument(
        "--authority-decay",
        type=float,
        default=0.85,
        help="Multiplicative authority decay per soft-reject / empty-queue step.",
    )
    p.add_argument(
        "--authority-hard-decay",
        type=float,
        default=0.5,
        help="Multiplicative authority decay per hard-reject (out-of-bounds) step.",
    )

    # SmolVLA correction (the actual thing added to ACT).
    p.add_argument(
        "--smol-correction-limit",
        type=float,
        default=0.15,
        help="Per-joint maximum |authority*(smol_ema - act)| correction (rad).",
    )
    p.add_argument(
        "--correction-alpha",
        type=float,
        default=0.5,
        help="EMA weight of the correction toward its desired value per step.",
    )
    p.add_argument(
        "--correction-step-limit",
        type=float,
        default=0.02,
        help="Per-joint maximum correction change per control step (rad).",
    )

    # Final smoothing / safety.
    p.add_argument(
        "--global-lowpass-alpha",
        type=float,
        default=0.85,
        help="Final command EMA weight applied to both ACT and the blend.",
    )
    p.add_argument("--act-joint-step-limit", type=float, default=1.0)
    p.add_argument("--act-tracking-limit", type=float, default=3.0)
    p.add_argument("--start-joint-tolerance", type=float, default=0.12)
    p.add_argument("--start-gripper-tolerance", type=float, default=0.012)
    p.add_argument("--max-consecutive-rejects", type=int, default=500)
    p.add_argument("--countdown", type=int, default=5)
    p.add_argument(
        "--resume-current",
        action="store_true",
        help="Continue from a held in-trajectory pose instead of requiring ACT_START.",
    )
    p.add_argument("--capture-refills", type=Path, help="Save exact ACT refill inputs/chunks and separate actual targets after stopping (ACT or hybrid)")
    p.add_argument("--quality-profile", type=Path, help="Optional blue towel staging/visual stop heuristic; never changes actions or safety bounds")
    p.add_argument("--first-grasp-check", action="store_true", help="Record first-grasp load diagnostics")
    p.add_argument("--first-grasp-approach", action="store_true", help="Trial of bounded clearance during the initial pinch approach")
    p.add_argument("--finish-check", action="store_true", help="Stop after stable folded cloth, release and arm retreat")
    p.add_argument("--act-fp16", action="store_true", help="Use ROCm FP16 autocast for ACT inference")
    p.add_argument("--trace-file", type=Path, help="Buffer per-tick vectors in memory; write only after stopping")
    p.add_argument("--motion-profile", type=Path, required=True)
    p.add_argument("--observation-profile", type=Path,
                   help="Verified as-trained image contract; defaults beside motion profile")
    p.add_argument("--start-pose-file", type=Path, required=True)
    p.add_argument("--act-only", action="store_true", help="Keep reference ACT path; never resume Smol RTC")
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()
    if args.act_fp16 and args.backend != "rocm":
        p.error("ACT FP16 requires ROCm")
    if args.finish_check and (args.resume_current or args.quality_profile is not None):
        p.error("Finish check requires the task origin and uses its own visual observations")
    if args.fps <= 0 or args.max_actions < 1:
        p.error("fps and max-actions must be positive")
    if not 0 <= args.handoff_step < args.max_actions:
        p.error("handoff-step must be in [0, max-actions)")
    if args.first_grasp_check and args.resume_current:
        p.error("first-grasp check requires starting from the task origin")
    if args.first_grasp_approach and (args.resume_current or args.capture_refills
                                    or not args.act_fp16
                                    or (not args.act_only and args.handoff_step < 220)):
        p.error("first-grasp approach requires the native ROCm FP16 start with a separate ACT grasp phase")
    if not 1 <= args.execution_horizon < 50:
        p.error("execution-horizon must be in [1, 49]")
    if not 1 <= args.queue_threshold < 50:
        p.error("queue-threshold must be in [1, 49]")
    if not 0.0 < args.smol_lowpass_alpha <= 1.0:
        p.error("smol-lowpass-alpha must be in (0, 1]")
    if not 0.0 < args.global_lowpass_alpha <= 1.0:
        p.error("global-lowpass-alpha must be in (0, 1]")
    if not 0.0 < args.max_authority <= 1.0:
        p.error("max-authority must be in (0, 1]")
    if not 0.0 < args.authority_up_rate <= 1.0:
        p.error("authority-up-rate must be in (0, 1]")
    if not 0.0 < args.authority_decay <= 1.0:
        p.error("authority-decay must be in (0, 1]")
    if not 0.0 < args.authority_hard_decay <= 1.0:
        p.error("authority-hard-decay must be in (0, 1]")
    if args.smol_correction_limit <= 0 or args.correction_step_limit <= 0:
        p.error("smol-correction-limit and correction-step-limit must be positive")
    if not 0.0 < args.correction_alpha <= 1.0:
        p.error("correction-alpha must be in (0, 1]")
    return args


def action_dict(action: np.ndarray) -> dict[str, float]:
    return {k: float(v) for k, v in zip(ACTION_NAMES, action, strict=True)}


def finish_from_camera(check, camera, step, measured, previous, grippers, *, held_since=None,
                       first_fold_retry=False):
    # Capture a consistent frame and host timestamp from the existing reader.
    with camera.frame_lock:
        rgb = camera.latest_frame
        frame_id = camera.latest_frame_id
        captured_s = camera.latest_timestamp
    if rgb is None or captured_s is None:
        return None
    return check.update(rgb, frame_id=frame_id, captured_s=captured_s, now_s=time.perf_counter(),
                        step=step, measured=measured, previous=previous, grippers=grippers,
                        held_since=held_since, first_fold_retry=first_fold_retry)


def confirm_finish_while_held(check, wrapper, robot, hold, step, stop_requested):
    """Read fresh feedback and images after the measured-pose hold command."""
    if check.regrasp is None or not check.retreated(hold, holding=True):
        return None
    from fpga_feedback_age import FeedbackAgeClock
    # The normal sidecar path can use the stock SDK's cached receipt times.
    feedback_clock = FeedbackAgeClock(allow_wall_clock=True)
    held_since = time.perf_counter()
    deadline = held_since + 1.0
    camera = robot.cameras["middle"]
    while not stop_requested() and (remaining := deadline - time.perf_counter()) > 0:
        try:
            motors, stamps, _ = wrapper.get_feedback_snapshot(timeout=min(.05, remaining))
        except TimeoutError:
            check.window.clear()
        else:
            measured = positions(motors)
            joint_error, _ = max_parts(np.abs(measured - hold))
            if feedback_clock.age_us(stamps) <= 100_000 and joint_error <= .05:
                evidence = finish_from_camera(
                    check, camera, step, measured, hold, gripper_snapshot(robot), held_since=held_since,
                )
                if evidence is not None and not stop_requested():
                    return evidence
            else:
                check.window.clear()
        time.sleep(max(0., min(.04, deadline - time.perf_counter())))
    return None


def as_action(value: torch.Tensor, *, project_soft_limits: bool, projection_label='candidate') -> np.ndarray:
    result = value.detach().float().cpu().numpy().reshape(-1)
    if result.shape != (14,) or not np.isfinite(result).all():
        raise RuntimeError(f"Invalid action: shape={result.shape}, finite={np.isfinite(result).all()}")
    result = result.astype(np.float32, copy=project_soft_limits)
    # Piper's gripper driver uses abs(); make the intended physical boundary explicit.
    result[GRIPPER] = np.clip(result[GRIPPER], 0.0, 0.08)
    if project_soft_limits:
        violation = np.maximum(LOWER - result, result - UPPER)
        joint_violation = float(np.max(violation[ARM]))
        if 0.0 < joint_violation <= SOFT_LIMIT_PROJECTION_MARGIN:
            result[ARM] = np.clip(result[ARM], LOWER[ARM], UPPER[ARM])
            print(
                f"soft_limit_projection policy={projection_label} max_correction={joint_violation:.6f} rad",
                flush=True,
            )
    return result



def capture_act_start(frame, obs, act_action, target, measured, previous, features, pre, args, policy=None):
    """Capture unchanged ACT result; optional same-frame A/B never sends targets."""
    import json
    import cv2
    from lerobot.processor.normalize_processor import NormalizerProcessorStep
    from training_effort_adapter import TrainingConstantEffortStep
    folder = Path(__file__).resolve().parents[1] / "logs" / ("act_start_" + str(time.time_ns()))
    folder.mkdir(parents=True)
    state = np.asarray(frame["observation.state"], dtype=np.float32)
    report = dict(execute=False, act_model=str(args.act_model),
                  observation_names=features["observation.state"]["names"], observation_state=state.tolist(),
                  measured_positions=measured.tolist(), previous_target=previous.tolist(),
                  act_action_before_ema=act_action.tolist(), smoothed_target=target.tolist(),
                  joint_step_limit=args.act_joint_step_limit,
                  joints=[dict(name=ACTION_NAMES[int(i)], current=float(measured[i]),
                               act=float(act_action[i]), target=float(target[i]),
                               delta_rad=float(target[i]-previous[i]),
                               delta_deg=float((target[i]-previous[i])*180/np.pi)) for i in ARM],
                  state_normalization=[], images={}, targets_dispatched=False,
                  image_adaptation=getattr(pre, "image_adaptation", None))
    normalization_input = state.copy()
    report["effort_adapters"] = []
    for step in pre.steps:
        if isinstance(step, TrainingConstantEffortStep):
            normalization_input = step.observation({"observation.state":normalization_input})["observation.state"]
            report["effort_adapters"].extend(step.description())
    report["input_after_effort_adapter"] = normalization_input.tolist()
    for step in pre.steps:
        if not isinstance(step, NormalizerProcessorStep):
            continue
        stats = step.state_dict()
        stats = {k.removeprefix("observation.state."): v.detach().float().cpu().numpy().reshape(-1)
                 for k,v in stats.items() if k.startswith("observation.state.")}
        mode = step.norm_map.get(step.features["observation.state"].type)
        diagnostic = dict(mode=getattr(mode, "value", str(mode)),
                          stats={k:v.tolist() for k,v in stats.items()}, eps=step.eps)
        if diagnostic["mode"] == "MEAN_STD" and "mean" in stats and "std" in stats:
            z = (normalization_input-stats["mean"])/(stats["std"]+step.eps)
            diagnostic["raw_normalized_state_without_adapter"] = ((state-stats["mean"])/(stats["std"]+step.eps)).tolist()
            diagnostic["reconstructed_normalized_state"] = z.tolist()
            diagnostic["largest_abs_normalized_inputs"] = [
                dict(name=report["observation_names"][int(i)], value=float(normalization_input[i]), raw_value=float(state[i]),
                     mean=float(stats["mean"][i]), std=float(stats["std"][i]), normalized=float(z[i]))
                for i in np.argsort(np.abs(z))[-8:][::-1]]
        report["state_normalization"].append(diagnostic)
    import os
    if os.environ.get("ACT_INPUT_AB") == "1" and policy is not None:
        # Same captured frame/checkpoint, no raw-action send and no pipeline toggle.
        from contextlib import nullcontext
        from lerobot.policies.utils import prepare_observation_for_inference
        raw_pre, raw_post = make_pre_post_processors(policy.config, str(args.act_model),
             preprocessor_overrides={"device_processor":{"device":args.backend_device}})
        from training_image_adapter import restore_captured_image_adapter
        restore_captured_image_adapter(raw_pre, report)
        batch = prepare_observation_for_inference(dict(frame), torch.device(args.backend_device), args.task, "piper_dual")
        amp = torch.autocast(device_type=args.backend_device) if policy.config.use_amp else nullcontext()
        with torch.inference_mode(), amp:
            raw_chunk = raw_post(policy.predict_action_chunk(raw_pre(batch)))
        raw_action = as_action(raw_chunk[0,0], project_soft_limits=False)
        raw_target = apply_global_smoothing(raw_action, previous, raw_action[GRIPPER], args.global_lowpass_alpha)
        report["same_frame_ab"] = dict(raw_first_action=raw_action.tolist(),
             raw_first_target_joint_max_rad=float(np.max(np.abs(raw_target[ARM]-previous[ARM]))),
             adapted_first_action=act_action.tolist(),
             adapted_first_target_joint_max_rad=float(np.max(np.abs(target[ARM]-previous[ARM]))),
             targets_dispatched=False)
    for role in ("left", "middle", "right"):
        image = obs[role]
        path = folder/(role+".png")
        if not cv2.imwrite(str(path), cv2.cvtColor(image, cv2.COLOR_RGB2BGR)):
            raise RuntimeError("Diagnostic snapshot could not be saved: "+str(path))
        report["images"][role] = dict(path=str(path), shape=list(image.shape), dtype=str(image.dtype),
                                     mean_rgb=np.mean(image, axis=(0,1)).tolist())
    path = folder/"report.json"
    path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print("ACT_START_DIAGNOSTIC="+json.dumps(dict(report=str(path),
          joints=sorted(report["joints"], key=lambda r:abs(r["delta_rad"]), reverse=True),
          same_frame_ab=report.get("same_frame_ab"), effort_adapters=report["effort_adapters"],
          normalized_input_summary=[d.get("largest_abs_normalized_inputs") for d in report["state_normalization"]])), flush=True)

def main() -> int:
    args = parse_args()
    import os
    handover_recovery_enabled = os.environ.get("AIPC_TOWEL_RELEASE_RECOVERY") == "1"
    if handover_recovery_enabled and (not args.finish_check or not args.act_fp16 or args.capture_refills):
        raise ValueError("Left release recovery requires the normal FP16 fold monitor")
    if os.environ.get('AIPC_FPGA_AUTHORITY_CONFIG'):
        if args.finish_check or args.act_fp16 or args.first_grasp_approach or handover_recovery_enabled:
            raise ValueError('FPGA authority requires the qualified ACT and termination settings')
        from fpga_action_authority import load_execution_config
        load_execution_config(os.environ['AIPC_FPGA_AUTHORITY_CONFIG'],execute=args.execute,max_actions=args.max_actions,
                    handoff_step=args.handoff_step,fps=args.fps,quality_profile=args.quality_profile)
    import blend_core as reference_core
    from cobot_motion_adapter import load_motion_profile, configure_bounds, configure_act, constrain_correction
    motion = load_motion_profile(args.motion_profile, reference_core)
    configure_bounds(motion, reference_core)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from portable_backend import select_device, seed_all, smol_config, place_policy
    device = select_device(args.backend)
    args.backend_device = str(device)
    period = 1.0 / args.fps
    stop = False

    def request_stop(_signum, _frame):
        nonlocal stop
        stop = True
        print("Stop requested; current measured pose will be held.", flush=True)

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    if args.handoff_file.exists():
        args.handoff_file.unlink()

    seed_all(args.seed, device)

    from lerobot.policies.act.configuration_act import ACTConfig
    act_config = configure_act(ACTConfig.from_pretrained(str(args.act_model)), motion)
    act_config.device = str(device)
    if args.act_fp16:
        act_config.use_amp = True
    act = ACTPolicy.from_pretrained(str(args.act_model), config=act_config)
    place_policy(act, device, args.precision)
    act_pre, act_post = make_pre_post_processors(
        act.config, str(args.act_model),
        preprocessor_overrides={"device_processor": {"device": str(device)}},
    )

    print(f"Loading SmolVLA candidate: {args.smol_model}", flush=True)
    smol = SmolVLAPolicy.from_pretrained(str(args.smol_model), config=smol_config(args.smol_model, device))
    place_policy(smol, device, args.precision)
    original_predict = smol.predict_action_chunk

    def deterministic_predict(batch, noise=None, **kwargs):
        if noise is None:
            seed_all(args.seed, device)
        return original_predict(batch, noise=noise, **kwargs)

    smol.predict_action_chunk = deterministic_predict
    rtc_cfg = RTCConfig(
        enabled=True,
        mode="guided",
        prefix_attention_schedule=RTCAttentionSchedule.EXP,
        max_guidance_weight=args.guidance_weight,
        execution_horizon=args.execution_horizon,
        debug=False,
    )
    smol.config.rtc_config = rtc_cfg
    smol.init_rtc_processor()
    smol_pre, smol_post = make_pre_post_processors(
        smol.config, str(args.smol_model),
        preprocessor_overrides={"device_processor": {"device": str(device)}},
    )
    from training_image_adapter import install_training_image_adapter
    image_profile = args.observation_profile or args.motion_profile.with_name("cobot_observation.json")
    install_training_image_adapter(act_pre, image_profile, args.act_model, "act")
    install_training_image_adapter(smol_pre, image_profile, args.smol_model, "smolvla")

    cameras = {
        "left": OpenCVCameraConfig(index_or_path=args.left_index, width=640, height=480, fps=30),
        "middle": OpenCVCameraConfig(index_or_path=args.middle_index, width=640, height=480, fps=30),
        "right": OpenCVCameraConfig(index_or_path=args.right_index, width=640, height=480, fps=30),
    }
    robot = PIPERDual(PIPERDualConfig(
        left_port=args.left_can,
        right_port=args.right_can,
        cameras=cameras,
        read_only=not args.execute,
    ))
    wrapper = ThreadSafeRobot(robot)
    act_features = {
        **hw_to_dataset_features(robot.action_features, "action"),
        **hw_to_dataset_features(robot.observation_features, "observation", use_video=False),
    }
    from training_effort_adapter import install_training_effort_adapter
    state_names = act_features["observation.state"]["names"]
    install_training_effort_adapter(act_pre, state_names, "ACT")
    install_training_effort_adapter(smol_pre, state_names, "SmolVLA")
    ordered_actions = list(robot.action_features)
    # Saved absolute-action ACT and its training adapters have stateless
    # observation processing. Keep the full preprocessing path for captures.
    cached_act = (args.act_fp16 and args.capture_refills is None
                  and act.config.temporal_ensemble_coeff is None)
    fused_act_backbone = False
    if cached_act:
        from act_refill_backbone import optimize_act_refill_backbone
        fused_act_backbone = optimize_act_refill_backbone(act.model)
    grasp_approach = None
    if args.first_grasp_approach:
        if not cached_act or act.config.n_action_steps != 50:
            raise ValueError("First-grasp approach requires the native 50-action ACT queue")
        from first_grasp_approach import FirstGraspApproach
        grasp_approach = FirstGraspApproach(act_post, act.config.n_action_steps, LOWER, UPPER,
                                            args.global_lowpass_alpha)
        act.on_action_refill = grasp_approach.on_refill
    act_engine = SyncInferenceEngine(
        policy=act,
        preprocessor=act_pre,
        postprocessor=act_post,
        dataset_features=act_features,
        ordered_action_keys=ordered_actions,
        task=args.task,
        device=str(device),
        robot_type=robot.robot_type,
        queued_action_ready=(lambda: bool(act._action_queue)) if cached_act else None,
    )
    smol_engine = RTCInferenceEngine(
        policy=smol,
        preprocessor=smol_pre,
        postprocessor=smol_post,
        robot_wrapper=wrapper,
        rtc_config=rtc_cfg,
        hw_features=act_features,
        task=args.task,
        fps=args.fps,
        device=str(device),
        use_torch_compile=False,
        rtc_queue_threshold=args.queue_threshold,
    )

    quality_monitor = None
    finish_check = None
    handover_check = None
    if args.quality_profile is not None:
        from towel_quality import TowelQualityMonitor
        quality_monitor = TowelQualityMonitor(args.quality_profile)
    if args.finish_check:
        from fold_handover import BRIDGE_STEPS, FoldHandoverCheck, longest_open, suitable_fold_view, usable_recovery
        from lerobot.policies.utils import prepare_observation_for_inference
        handover_check = FoldHandoverCheck()
    if handover_recovery_enabled and (not cached_act or act.config.n_action_steps != 50):
        raise ValueError("Left release recovery requires the native 50-action ACT queue")
    termination_reason = "action_budget"
    first_grasp_check = first_grasp_miss = None
    wrist_corner_check = None
    first_grasp_load_drops = {}
    if args.first_grasp_check:
        from first_grasp_check import FirstGraspCheck, WristCornerCheck
        first_grasp_check = FirstGraspCheck()
        wrist_corner_check = WristCornerCheck()
    execution_failure = None
    failure_context = None
    exit_hold_receipt = None
    connection_report = None
    exit_grippers = None
    refill_recorder = None
    if args.capture_refills is not None:
        from act_refill_capture import ActRefillCapture
        refill_recorder = ActRefillCapture(act, act_pre, act_post, act_features, ordered_actions,
            args.capture_refills, execute=args.execute, fps=args.fps,
            context=dict(task=args.task, execution_settings=vars(args),
                         control_mode="act" if args.act_only else "hybrid",
                         capture_scope="Exact ACT refill inputs/chunks; dispatched targets may include SmolVLA arm corrections",
                         smol_inputs_captured=False), model_dir=args.act_model)
        refill_recorder.install()
    telemetry = create_run(execute=args.execute, max_actions=args.max_actions,
                             max_authority=args.max_authority)
    connected = False
    fpga_shadow = None
    fpga_fold_observer = None
    fpga_action_authority = None
    authority_diagnostic_targets = []
    fpga_feedback_clock = None
    smol_started = False
    smol_active = False
    handoff_cancelled = False
    previous: np.ndarray | None = None
    correction = np.zeros(14, dtype=np.float32)
    authority = 0.0
    accepted = rejected = fallback = 0
    consecutive_rejects = 0

    print(
        f"Mode={'EXECUTE' if args.execute else 'DRY-RUN'} max_actions={args.max_actions} "
        f"handoff_step={args.handoff_step} max_authority={args.max_authority:.2f}",
        flush=True,
    )
    try:
        if os.environ.get('AIPC_FPGA_AUTHORITY_CONFIG'):
            from fpga_action_authority import FpgaActionAuthority
            from fpga_feedback_age import FeedbackAgeClock
            fpga_action_authority=FpgaActionAuthority.from_environment(args)
            fpga_action_authority.start(countdown=args.countdown if args.execute else 0,stop_requested=lambda:stop)
            fpga_action_authority.attach_telemetry()
            fpga_feedback_clock=FeedbackAgeClock()
        if os.environ.get("AIPC_FPGA_SHADOW"):
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "npu_deploy"))
                from fpga_live_shadow import from_environment
                fpga_shadow = from_environment(max_actions=args.max_actions, handoff_step=args.handoff_step)
                if os.environ.get("AIPC_FPGA_REQUIRE_START") == "1":
                    fpga_shadow.probe()
            except Exception as shadow_error:
                print("FPGA_SHADOW_START_ERROR="+repr(shadow_error), flush=True)
                if os.environ.get("AIPC_FPGA_REQUIRE_START") == "1":
                    raise
        if stop:
            raise KeyboardInterrupt
        connection_report = connect_holding_measured(robot, stop_requested=lambda: stop,
            before_enable=fpga_action_authority.validate_initial if fpga_action_authority is not None else None)
        connected = True
        obs = wrapper.get_observation()
        measured = positions(obs)
        previous = measured.copy()
        if args.finish_check:
            from towel_finish import TowelFinishCheck
            finish_check = TowelFinishCheck(obs["middle"])
        import json
        ACT_START = np.asarray(json.loads(args.start_pose_file.read_text())["target"], dtype=np.float32)
        if ACT_START.shape != (14,) or not np.isfinite(ACT_START).all():
            raise ValueError("Own task start must contain 14 finite values")

        start_joint, start_grip = max_parts(np.abs(measured - ACT_START))
        print(
            f"Initial pose error: joint={start_joint:.6f} gripper={start_grip:.6f}",
            flush=True,
        )
        if args.execute and not args.resume_current and (
            start_joint > args.start_joint_tolerance
            or start_grip > args.start_gripper_tolerance
        ):
            raise RuntimeError(
                "Start pose is not the proven start. Run reset_piper_pose.py first. "
                f"joint={start_joint:.6f}, gripper={start_grip:.6f}"
            )
        if args.execute and args.resume_current:
            print(
                "RESUME_CURRENT enabled: treating the measured held pose as an "
                "in-trajectory ACT observation.",
                flush=True,
            )

        act_engine.start()
        smol_engine.start()
        smol_started = True
        # RTC remains paused until the handoff point.

        if args.execute and fpga_action_authority is None:
            for n in range(args.countdown, 0, -1):
                print(f"Hybrid execution starts in {n}...", flush=True)
                time.sleep(1)

        if quality_monitor is not None:
            import json
            start_check = quality_monitor.check_start(wrapper.get_observation()["middle"])
            print("TOWEL_START_CHECK="+json.dumps(start_check), flush=True)
        if os.environ.get("AIPC_FPGA_FOLD") and fpga_action_authority is None:
            try:
                if os.environ.get("AIPC_FPGA_SHADOW"):
                    raise ValueError("AIPC_FPGA_SHADOW and AIPC_FPGA_FOLD cannot run together")
                from fpga_feedback_age import FeedbackAgeClock
                from fpga_fold_observer import FpgaFoldObserver
                fpga_fold_observer = FpgaFoldObserver.from_environment(
                    robot.cameras["middle"], args.quality_profile)
                fpga_feedback_clock = FeedbackAgeClock()
                fpga_fold_observer.start(
                    fpga_fold_observer.guard_profile["start"],
                    quality_monitor.baseline_area if quality_monitor is not None else None)
                if os.environ.get("AIPC_FPGA_REQUIRE_START") == "1":
                    fpga_fold_observer.wait_ready()
                print("FPGA_FOLD_OBSERVER=enabled address="+
                      str(fpga_fold_observer.address), flush=True)
            except Exception as observer_error:
                print("FPGA_FOLD_OBSERVER_START_ERROR="+repr(observer_error), flush=True)
                if fpga_fold_observer is not None:
                    fpga_fold_observer.close()
                fpga_fold_observer = None
                if os.environ.get("AIPC_FPGA_REQUIRE_START") == "1":
                    raise
        completed_steps = 0
        act_pipeline_timings = []
        refill_timings = []
        trace_rows = []
        print(f"ACT_EXECUTION_CONFIG mode=saved_checkpoint chunk_size={act.config.chunk_size} "
              f"n_action_steps={act.config.n_action_steps} coeff={act.config.temporal_ensemble_coeff} "
              f"fp16={args.act_fp16} fused_backbone={fused_act_backbone}", flush=True)
        max_target_joint_step = max_tracking_error = 0.0
        execution_started = time.perf_counter()
        retry_first_fold = (args.execute and fpga_action_authority is None
                            and os.environ.get("AIPC_FIRST_FOLD_RETRY_HOLD") == "1")
        retry_first_fold_step = max(300, args.handoff_step)
        telemetry.state("running")
        for step in range(args.max_actions):
            if stop:
                break
            if fpga_action_authority is not None and fpga_action_authority.trial_finished(step):
                termination_reason='commissioning_limit'
                if fpga_action_authority.trial.plan['stop_mode'] in ('pl_veto','reply_loss'):
                    failure_context=dict(step=step,stage='commissioning_fault',dispatched=False)
                    def fault_feedback():
                        fresh,timestamps,sampled_ns=wrapper.get_feedback_snapshot(timeout=fpga_action_authority.config['reply_budget_us']/1e6)
                        return positions(fresh),fpga_feedback_clock.age_us(timestamps),sampled_ns
                    fpga_action_authority.trial.inject_fault(fpga_action_authority,fault_feedback,time.perf_counter()-execution_started)
                if fpga_action_authority.trial.plan['stop_mode']=='operator_stop':stop=True
                print('FPGA_COMMISSIONING_LIMIT='+str(step),flush=True)
                break
            tick = time.perf_counter()
            failure_context = dict(step=step, stage="observation", tick_monotonic_s=tick, dispatched=False)
            obs = wrapper.get_observation()
            fpga_observation_elapsed = time.perf_counter()-execution_started
            measured = positions(obs)
            if quality_monitor is not None and quality_monitor.update(obs["middle"], tick-execution_started):
                termination_reason = "visual_finish_candidate"
                failure_context = None
                print("TOWEL_VISUAL_STOP: compact, clear and stable; fold success still needs review", flush=True)
                break
            check_first_grasp = (first_grasp_check is not None and not first_grasp_check.done
                                 and not smol_active and (args.act_only or step < args.handoff_step))
            grippers_before_action = (gripper_snapshot(robot)
                                      if args.trace_file is not None or check_first_grasp or finish_check is not None
                                      else None)
            request_left_release = False
            if finish_check is not None:
                had_incomplete_landing = finish_check.landing.incomplete is not None
                evidence = finish_from_camera(finish_check, robot.cameras["middle"], step,
                                              measured, previous, grippers_before_action,
                                              first_fold_retry=(retry_first_fold and first_grasp_miss is not None
                                                                and step >= retry_first_fold_step))
                if not had_incomplete_landing and finish_check.landing.incomplete is not None:
                    print("FIRST_FOLD_LANDING_INCOMPLETE=" + json.dumps(finish_check.landing.incomplete), flush=True)
                if evidence is not None:
                    termination_reason = "fold_finish_observed"
                    failure_context = None
                    print("TOWEL_FINISH_OBSERVED=" + json.dumps(evidence), flush=True)
                    break
                if finish_check.retry.result is not None:
                    termination_reason = "first_fold_retry"
                    failure_context = None
                    print("TOWEL_FIRST_FOLD_RETRY=" + json.dumps(finish_check.retry.result), flush=True)
                    break
                had_dual_hold = handover_check.dual_hold is not None
                had_empty_regrasp = handover_check.right_empty is not None
                had_unconfirmed_regrasp = handover_check.right_unconfirmed is not None
                had_recovered_regrasp = handover_check.right_regrasp_recovered is not None
                had_sweep_stall = handover_check.sweep_stalled is not None
                request_left_release = handover_check.observe(
                    time.perf_counter(), step, finish_check, previous, grippers_before_action,
                    measured=measured, time_left_s=(args.max_actions-step)/args.fps,
                )
                if not had_dual_hold and handover_check.dual_hold is not None:
                    print("FOLD_DUAL_HOLD=" + json.dumps(handover_check.dual_hold), flush=True)
                if not had_empty_regrasp and handover_check.right_empty is not None:
                    print("FOLD_RIGHT_REGRASP_EMPTY=" + json.dumps(handover_check.right_empty), flush=True)
                if not had_unconfirmed_regrasp and handover_check.right_unconfirmed is not None:
                    print("FOLD_RIGHT_REGRASP_UNCONFIRMED=" + json.dumps(handover_check.right_unconfirmed), flush=True)
                if not had_recovered_regrasp and handover_check.right_regrasp_recovered is not None:
                    print("FOLD_RIGHT_REGRASP_RECOVERED=" + json.dumps(handover_check.right_regrasp_recovered), flush=True)
                if not had_sweep_stall and handover_check.sweep_stalled is not None:
                    print("FOLD_SWEEP_STALLED=" + json.dumps(handover_check.sweep_stalled), flush=True)
            if check_first_grasp:
                for side, index in (("left", 6), ("right", 13)):
                    if getattr(first_grasp_check, side).closed_at is not None or previous[index] < .015:
                        continue
                    camera = robot.cameras[side]
                    with camera.frame_lock:
                        rgb, frame_id, captured_s = camera.latest_frame, camera.latest_frame_id, camera.latest_timestamp
                    if rgb is not None and captured_s is not None:
                        notice = wrist_corner_check.update(
                            side, rgb, frame_id, captured_s, time.perf_counter(), step,
                            previous[index], grippers_before_action.get(side, {}),
                        )
                        if notice is not None:
                            print("FIRST_GRASP_INSIDE_EDGE=" + json.dumps(dict(side=side, **notice)), flush=True)
                unloaded = first_grasp_check.update(tick, previous, grippers_before_action)
                for side, evidence in first_grasp_check.load_drops.items():
                    if side not in first_grasp_load_drops:
                        first_grasp_load_drops[side] = dict(step=step, **evidence)
                        notice = dict(side=side, **first_grasp_load_drops[side])
                        print("FIRST_GRASP_LOAD_DROP=" + json.dumps(notice), flush=True)
                        print("首抓已记录夹持负载，随后力矩降低，保留录像供确认。", flush=True)
                if unloaded:
                    if first_grasp_miss is None:
                        first_grasp_miss = dict(step=step, sides={})
                    for side, evidence in unloaded.items():
                        first_grasp_miss['sides'][side] = dict(step=step, **evidence)
                    print("FIRST_GRASP_MISS=" + json.dumps(dict(step=step, sides=unloaded)), flush=True)
                    print("首抓疑似空夹，已记录反馈，继续折叠流程。", flush=True)

            act_frame = build_dataset_frame(act_features, obs, prefix="observation")
            act_queue = getattr(act, "_action_queue", None)
            act_tick = time.perf_counter()
            accepted_release = False
            if (handover_recovery_enabled and request_left_release and act_queue and not smol_active
                    and (args.act_only or step + BRIDGE_STEPS < args.handoff_step)
                    and max_parts(np.abs(measured - previous))[0] < .08):
                with torch.inference_mode(), torch.autocast(device_type=device.type):
                    pending = act_post(torch.stack(tuple(act_queue), dim=1))[0].float().cpu().numpy()
                    shape = finish_check.geometry(obs["middle"])
                    if longest_open(pending[:, 6]) >= 8 or not suitable_fold_view(shape, finish_check.initial):
                        handover_check.record_probe(act_tick, step, False, None)
                    else:
                        inputs = prepare_observation_for_inference(dict(act_frame), device, args.task, robot.robot_type)
                        normalized = act.predict_action_chunk(act_pre(inputs))[:, :act.config.n_action_steps]
                        physical = act_post(normalized)[0].float().cpu().numpy()
                        accepted_release = usable_recovery(physical, previous, LOWER, UPPER,
                                                           args.global_lowpass_alpha, args.fps)
                        peak_mm = float(np.max(physical[:, 6]) * 1000) if np.isfinite(physical).all() else None
                        handover_check.record_probe(act_tick, step, accepted_release, peak_mm)
                        if accepted_release:
                            act_queue.clear()
                            act_queue.extend(normalized.transpose(0, 1))
                print("FOLD_LEFT_RELEASE_PROBE=" + json.dumps(handover_check.probes[-1]), flush=True)
            act_refill = None if act_queue is None else not act_queue or accepted_release
            if args.capture_refills is not None:
                refill_recorder.before_get_action(step, act_refill, act_frame, tick_s=tick,
                    previous=previous, cameras=robot.cameras)
            act_tensor = act_engine.get_action(act_frame)
            if args.capture_refills is not None and act_tensor is not None:
                refill_recorder.after_get_action(step, act_tensor)
            act_pipeline_s = time.perf_counter() - act_tick
            act_pipeline_timings.append(act_pipeline_s)
            if act_refill is True:
                refill_timings.append(act_pipeline_s)
            if act_tensor is None:
                raise RuntimeError("guard returned no action")
            act_action = as_action(act_tensor, project_soft_limits=False)
            act_before_projection = act_action.copy()
            if fpga_action_authority is not None:
                # Reuse the existing <=0.02 rad joint-only projection. Targets
                # remain inside the unchanged CPU/PL bounds; larger violations
                # still reach the original strict validator and are rejected.
                act_action = as_action(act_tensor, project_soft_limits=True, projection_label='ACT')
            failure_context.update(stage="blend_or_ACT_base_validation", measured=measured.tolist(),
                                   previous=previous.tolist(), act_action=act_action.tolist())
            import os
            if step == 0 and not args.execute and os.environ.get("ACT_INPUT_CAPTURE") == "1":
                start_preview = apply_global_smoothing(act_action, previous, act_action[GRIPPER], args.global_lowpass_alpha)
                capture_act_start(act_frame, obs, act_action, start_preview, measured, previous, act_features, act_pre, args, act)

            trigger = (
                not args.act_only
                and not handoff_cancelled
                and (step >= args.handoff_step or args.handoff_file.exists())
            )
            if trigger and not smol_active:
                if args.handoff_file.exists():
                    args.handoff_file.unlink()
                smol_engine.reset()
                smol_engine.notify_observation(obs)
                smol_engine.resume()
                smol_active = True
                consecutive_rejects = 0
                print(f"HANDOFF_REQUEST step={step}: SmolVLA blend mode enabled", flush=True)

            source = "SAFE"
            detail = "primary"
            previous_correction = correction.copy()
            raw_candidate = None
            if smol_active:
                smol_engine.notify_observation(obs)
                if smol_engine.failed:
                    raise RuntimeError(f"SmolVLA RTC failed:\n{smol_engine.failure_traceback}")
                candidate_tensor = smol_engine.get_action(None)
                if candidate_tensor is None:
                    raw_candidate = None
                else:
                    raw_candidate = as_action(
                        candidate_tensor,
                        project_soft_limits=True,
                    )
                previous_correction = correction.copy()
                res = blend_step(
                    act_action,
                    raw_candidate,
                    previous,
                    correction,
                    authority,
                    args,
                )
                source = res.source
                authority = res.authority
                correction = res.correction
                detail = res.detail
                accepted += res.accepted
                rejected += res.rejected
                fallback += res.fallback
                if res.rejected:
                    consecutive_rejects += 1
                else:
                    consecutive_rejects = 0
                if res.source != "SmolVLA":
                    if consecutive_rejects >= args.max_consecutive_rejects:
                        smol_engine.pause()
                        smol_engine.reset()
                        smol_active = False
                        handoff_cancelled = True
                        detail += "; takeover cancelled, safe-only"

            proposed_correction = correction.copy()
            correction = constrain_correction(act_action, correction, previous_correction, args, reference_core)
            if not np.array_equal(proposed_correction, correction):
                detail += "; bounded_to_task_envelope"
            detail += f" act_ms={act_pipeline_s*1000:.2f}"
            # Blend onto the trusted guard base; gripper stays ACT-owned.
            raw_target = act_action.copy()
            raw_target[ARM] = act_action[ARM] + correction[ARM]
            raw_target[GRIPPER] = act_action[GRIPPER]

            # Final global smoothing; re-assert ACT gripper (no lag on gripper).
            adapting_grasp = (grasp_approach is not None and not smol_active
                              and (args.act_only or step < args.handoff_step))
            smoothing_base = (grasp_approach.previous_base if adapting_grasp
                              and grasp_approach.previous_base is not None else previous)
            target = apply_global_smoothing(
                raw_target, smoothing_base, act_action[GRIPPER], args.global_lowpass_alpha
            )
            if adapting_grasp:
                target = grasp_approach.apply(target)
            if handover_check is not None:
                target = handover_check.apply_bridge(target, previous)

            failure_context.update(stage="final_validation", target=target.tolist(),
                                   correction=correction.tolist(), source=source)
            validate_absolute(target)  # Same task envelope for ACT and hybrid.
            # The command carries a SmolVLA correction (even a decaying one), so
            # validate it in the strict blended mode; pure-ACT ticks keep the
            # proven trusted-ACT semantics exactly.
            max_correction_rad = float(np.max(np.abs(correction[ARM])))
            blended = max_correction_rad > 1e-4
            joint_step, grip_step, tracking, grip_tracking = validate_execution(
                target,
                measured,
                previous,
                args,
                trusted_act=not blended,
                correction=correction if blended else None,
                previous_correction=previous_correction if blended else None,
            )
            # A stop may arrive while cameras or inference are busy; do not send
            # the newly computed target once the request has been observed.
            if stop:
                failure_context = None
                break
            if args.execute or fpga_action_authority is not None:
                if fpga_action_authority is not None:
                    from host.action_authority import AuthorityDenied
                    from fpga_action_authority import dispatch_policy_target,policy_target_dispatch
                    failure_context['stage']='fpga_authority'
                    # Inference can consume most of a tick: re-read and re-check
                    # feedback instead of authorizing against a stale snapshot.
                    def authority_feedback():
                        fresh_observation,timestamps,sampled_ns=wrapper.get_feedback_snapshot(
                            timeout=fpga_action_authority.config['reply_budget_us']/1e6)
                        fresh=positions(fresh_observation)
                        age=fpga_feedback_clock.age_us(timestamps)
                        return fresh,age,sampled_ns
                    try:
                        dispatch_deadline_ns=time.perf_counter_ns()+fpga_action_authority.config['reply_budget_us']*1000
                        dispatch_policy_target(fpga_action_authority,step=step,elapsed_s=time.perf_counter()-execution_started,
                            target=target,previous=previous,act=act_action,
                            correction=correction,previous_correction=previous_correction,blended=blended,
                            feedback_reader=authority_feedback,
                            cpu_validator=lambda fresh:validate_execution(target,fresh,previous,args,trusted_act=not blended,
                                correction=correction if blended else None,
                                previous_correction=previous_correction if blended else None),
                            queue_depth=smol_engine.action_queue.qsize() if smol_engine.action_queue is not None else 0,
                            dispatch=policy_target_dispatch(args.execute,
                                lambda frozen:wrapper.send_action_before(action_dict(frozen),dispatch_deadline_ns,
                                    stop_requested=lambda:stop),authority_diagnostic_targets),
                            stop_requested=lambda:stop)
                    except AuthorityDenied as denied:
                        failure_context['authority']=denied.record
                        if stop and denied.reason=='stop_requested':
                            failure_context=None;break
                        raise
                    failure_context['authority']=dict(fpga_action_authority.gate.records[-1])
                else:
                    failure_context["stage"] = "dispatch"
                    wrapper.send_action(action_dict(target))
                failure_context["dispatched"] = bool(args.execute)
            telemetry.tick(step=step, authority=authority, max_correction_rad=max_correction_rad,
                           source=source, dispatched=bool(args.execute))
            if fpga_shadow is not None:
                try:
                    fpga_shadow.submit(step=step, elapsed=tick-execution_started,
                        target=target, measured=measured, previous=previous, act=act_action,
                        correction=correction, previous_correction=previous_correction,
                        blended=blended, dispatched=bool(args.execute))
                except Exception as shadow_error:
                    print("FPGA_SHADOW_ERROR="+repr(shadow_error), flush=True)
            if fpga_fold_observer is not None:
                try:
                    age_us = fpga_feedback_clock.age_us(robot.last_feedback_timestamps)
                    fpga_fold_observer.submit(step=step, elapsed_s=fpga_observation_elapsed,
                        target=target, measured=measured, previous=previous, act=act_action,
                        correction=correction, previous_correction=previous_correction,
                        blended=blended, feedback_age_us=age_us, dispatched=bool(args.execute),
                        queue_depth=(smol_engine.action_queue.qsize()
                                     if smol_engine.action_queue is not None else 0))
                except Exception as observer_error:
                    print("FPGA_FOLD_OBSERVER_SUBMIT_ERROR="+repr(observer_error), flush=True)
            if args.capture_refills is not None:
                refill_recorder.after_dispatch(step, act_action, target, measured, previous,
                    tick_s=tick, returned_s=time.perf_counter(), grippers=grippers_before_action, dispatched=args.execute)
            if args.trace_file is not None:
                trace_rows.append(dict(step=step, tick_monotonic_s=tick, act_done_monotonic_s=act_tick+act_pipeline_s, dispatch_done_monotonic_s=time.perf_counter(), act_refill=act_refill, act_pipeline_s=act_pipeline_s, source=source, detail=detail, gripper_feedback_before_action=grippers_before_action, authority=authority, measured=measured.tolist(), previous=previous.tolist(), act_action=act_action.tolist(), act_before_projection=act_before_projection.tolist(), smol_candidate=(raw_candidate.tolist() if raw_candidate is not None else None), proposed_correction=proposed_correction.tolist(), correction=correction.tolist(), target=target.tolist()))
            max_target_joint_step = max(max_target_joint_step, joint_step)
            max_tracking_error = max(max_tracking_error, tracking)
            previous = target
            failure_context = None
            completed_steps += 1
            queue = smol_engine.action_queue.qsize() if smol_engine.action_queue is not None else 0
            print(
                f"step={step:04d} source={source} joint_step={joint_step:.5f} "
                f"tracking={tracking:.5f} queue={queue} "
                f"authority={authority:.2f} corr={float(np.max(np.abs(correction[ARM]))):.4f} "
                f"{detail}",
                flush=True,
            )

            elapsed = time.perf_counter() - tick
            if elapsed < period:
                time.sleep(period - elapsed)

        if connected and args.execute:
            if fpga_action_authority is not None:fpga_action_authority.exiting('stop_requested' if stop else '')
            if fpga_action_authority is not None:
                from policy_exit import hold_current
                exit_hold_receipt=hold_current(wrapper,positions,action_dict,lambda:gripper_snapshot(robot),
                    budget_us=fpga_action_authority.config['reply_budget_us'],feedback_limit_us=fpga_action_authority.config['feedback_limit_us'])
                print('POLICY_EXIT_HOLD='+json.dumps(exit_hold_receipt),flush=True)
                if not exit_hold_receipt['command_returned']:raise RuntimeError('Native exit hold did not return; physical hold unconfirmed')
            else:
                hold = positions(wrapper.get_observation())
                wrapper.send_action(action_dict(hold))
                print("Current measured pose is held.", flush=True)
                budget_reached = (finish_check is not None and termination_reason == "action_budget"
                                  and completed_steps == args.max_actions)
                if budget_reached and not stop:
                    evidence = confirm_finish_while_held(finish_check, wrapper, robot, hold,
                                                         completed_steps, lambda: stop)
                    if evidence is not None:
                        termination_reason = "fold_finish_observed"
                        print("TOWEL_FINISH_OBSERVED=" + json.dumps(evidence), flush=True)
                if not stop and termination_reason == "fold_finish_observed":
                    print("TOWEL_FINISH_HOLD", flush=True)
                elif not stop and termination_reason == "first_fold_retry":
                    print("TOWEL_FIRST_FOLD_HOLD", flush=True)
                elif budget_reached and not stop:
                    print("TOWEL_BUDGET_HOLD", flush=True)
        elapsed_run = time.perf_counter() - execution_started
        print(f"COBOT_RUN_SUMMARY steps={completed_steps}/{args.max_actions} manual_stop={stop} "
              f"elapsed_s={elapsed_run:.3f} mean_hz={completed_steps/max(elapsed_run,1e-9):.3f} execute={args.execute}", flush=True)
        print(f"ACT_EXECUTION_SUMMARY mode=saved_checkpoint n_action_steps={act.config.n_action_steps} "
              f"coeff={act.config.temporal_ensemble_coeff} chunk_size={act.config.chunk_size} "
              f"pipeline_p95_ms={np.quantile(act_pipeline_timings,.95)*1000 if act_pipeline_timings else 0:.2f} "
              f"ticks_over_budget={sum(t>period for t in act_pipeline_timings)} "
              f"refills={len(refill_timings)} refill_p95_ms={np.quantile(refill_timings,.95)*1000 if refill_timings else 0:.2f} "
              f"max_target_step_rad={max_target_joint_step:.6f} max_tracking_rad={max_tracking_error:.6f}", flush=True)
        print(
            f"HYBRID_COMPLETE accepted_smol={accepted} rejected_smol={rejected} "
            f"act_fallback={fallback}",
            flush=True,
        )
        return 130 if stop else 0
    except Exception as execution_error:
        execution_failure = dict(error=type(execution_error).__name__+": "+str(execution_error), context=failure_context)
        termination_reason = "error"
        if connected and args.execute:
            if fpga_action_authority is not None:fpga_action_authority.exiting((fpga_action_authority.gate.fault if fpga_action_authority.gate else None) or 'control_error')
            try:
                if fpga_action_authority is not None:
                    if exit_hold_receipt is None:
                        from policy_exit import hold_current
                        exit_hold_receipt=hold_current(wrapper,positions,action_dict,lambda:gripper_snapshot(robot),
                            budget_us=fpga_action_authority.config['reply_budget_us'],feedback_limit_us=fpga_action_authority.config['feedback_limit_us'])
                        print('POLICY_EXIT_HOLD='+json.dumps(exit_hold_receipt),flush=True)
                else:
                    hold = positions(wrapper.get_observation())
                    wrapper.send_action(action_dict(hold))
                    print("Safety exit: current measured pose is held.", flush=True)
            except Exception as exc:
                print(f"WARNING: hold failed: {exc}", flush=True)
        raise
    finally:
        failed = sys.exc_info()[0] is not None
        telemetry.state("error" if failed else ("stopped" if stop else "finished"))
        original_cleanup_completed = False
        try:
            if fpga_action_authority is not None:
                fpga_action_authority.close(outcome='error' if failed else 'stopped' if stop else 'ended')
                if not args.execute:
                    import json
                    print('FPGA_AUTHORITY_READONLY='+json.dumps(dict(
                        session=fpga_action_authority.client.session,
                        memory_receipts=len(authority_diagnostic_targets),robot_motion_commands_sent=False,
                        full_controller_load_measured=False,eligible_for_policy_execution=False)),flush=True)
            if fpga_fold_observer is not None:
                fpga_fold_observer.close(outcome='error' if failed else 'stopped' if stop else 'ended')
            if smol_started:
                smol_engine.stop()
            act_engine.stop()
            if connected:
                exit_grippers = gripper_snapshot(robot)
                import json
                print("POLICY_GRIPPER_EXIT="+json.dumps(exit_grippers), flush=True)
                robot.disconnect()
            if args.trace_file is not None:
                import json
                try:
                    args.trace_file.parent.mkdir(parents=True, exist_ok=True)
                    trace = dict(
                        act_model=str(args.act_model), smol_model=str(args.smol_model),
                        execute=args.execute, fps=args.fps, timestamp_basis="local time.perf_counter",
                        policy_connection=connection_report, gripper_exit=exit_grippers,
                        image_adapters=dict(act=getattr(act_pre, "image_adaptation", None),
                                            smol=getattr(smol_pre, "image_adaptation", None)),
                        termination_reason="manual_stop" if stop else termination_reason,
                        execution_failure=execution_failure, first_grasp_miss=first_grasp_miss,
                        first_grasp_load_drops=first_grasp_load_drops,
                        first_grasp_inside_edge=wrist_corner_check.evidence if wrist_corner_check is not None else {},
                        fold_handover=(dict(dual_hold=handover_check.dual_hold, right_empty=handover_check.right_empty,
                                            right_unconfirmed=handover_check.right_unconfirmed,
                                            right_regrasp_recovered=handover_check.right_regrasp_recovered,
                                            sweep_stalled=handover_check.sweep_stalled,
                                            release_probes=handover_check.probes) if handover_check is not None else None),
                        exit_hold=exit_hold_receipt, rows=locals().get("trace_rows", []),
                        quality_monitor=quality_monitor.report() if quality_monitor is not None else None,
                        finish_check=finish_check.report() if finish_check is not None else None,
                        act_fp16=args.act_fp16, fused_act_backbone=fused_act_backbone,
                        first_grasp_approach_ticks=grasp_approach.applied_ticks if grasp_approach is not None else None,
                        authority_run=(str(Path(fpga_action_authority.log.name).parent)
                                       if fpga_action_authority is not None else None),
                        commissioning=(fpga_action_authority.trial.plan if fpga_action_authority is not None
                                       and fpga_action_authority.trial is not None else None),
                    )
                    args.trace_file.write_text(json.dumps(trace, allow_nan=False), encoding="utf-8")
                    print("COBOT_TRACE="+str(args.trace_file), flush=True)
                except Exception as trace_error:
                    print("WARNING: trace write failed after shutdown: "+str(trace_error), flush=True)
            print("Piper disconnected; no disable command was sent.", flush=True)
            original_cleanup_completed = True
        finally:
            if fpga_shadow is not None:
                try:
                    fpga_shadow.close()
                except Exception as shadow_error:
                    print("FPGA_SHADOW_CLOSE_ERROR="+repr(shadow_error), flush=True)
            if refill_recorder is not None:
                refill_recorder.finish(run_context=dict(original_cleanup_completed=original_cleanup_completed,
                    stopped=stop, completed_steps=locals().get("completed_steps", 0),
                    trace_file=str(args.trace_file) if args.trace_file else None,
                    policy_connection=connection_report, gripper_exit=exit_grippers))


def validate_execution(
    target: np.ndarray,
    measured: np.ndarray,
    previous: np.ndarray,
    args: argparse.Namespace,
    *,
    trusted_act: bool,
    correction: np.ndarray | None = None,
    previous_correction: np.ndarray | None = None,
) -> tuple[float, float, float, float]:
    joint_step, grip_step = max_parts(np.abs(target - previous))
    tracking, grip_tracking = max_parts(np.abs(measured - previous))
    if trusted_act:
        # Pure ACT tick: preserve the proven ACT deployment semantics exactly.
        if joint_step > args.act_joint_step_limit:
            raise RuntimeError(
                f"Safety stop: trusted guard joint step {joint_step:.6f} > "
                f"{args.act_joint_step_limit:.6f}"
            )
        if tracking > args.act_tracking_limit:
            raise RuntimeError(
                f"Safety stop: trusted guard tracking {tracking:.6f} > "
                f"{args.act_tracking_limit:.6f}"
            )
        return joint_step, grip_step, tracking, grip_tracking

    # Blended tick: ACT is still the base (so ACT limits on step/tracking apply),
    # but SmolVLA influence is additionally absolutely limited and slew-bounded.
    validate_absolute(target)
    if joint_step > args.act_joint_step_limit:
        raise RuntimeError(
            f"Safety stop: blended joint step {joint_step:.6f} > "
            f"{args.act_joint_step_limit:.6f}"
        )
    if tracking > args.act_tracking_limit:
        raise RuntimeError(
            f"Safety stop: blended tracking {tracking:.6f} > "
            f"{args.act_tracking_limit:.6f}"
        )
    if correction is not None and previous_correction is not None:
        corr_step = float(np.max(np.abs(correction[ARM] - previous_correction[ARM])))
        # _apply_desired clips the slew to exactly correction_step_limit; float32
        # rounding can make the measured change land a hair over the bound, so
        # allow the same 1e-5 rad epsilon the offline test asserts (matches S3).
        if corr_step > args.correction_step_limit + 1e-5:
            raise RuntimeError(
                f"Safety stop: correction step {corr_step:.6f} > "
                f"{args.correction_step_limit + 1e-5:.6f}"
            )
    return joint_step, grip_step, tracking, grip_tracking


if __name__ == "__main__":
    raise SystemExit(main())
