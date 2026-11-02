"""Safely return the dual Piper arms to the demonstrated start pose.

This tool exists because a cold ``robot.connect()`` never commands a pose:
without an explicit command the arms would either stay in whatever pose they
are in (unknown) or, worse, drop. This script slowly moves both arms to the
training start pose while keeping them enabled the whole time.

Safety properties
-----------------
* **Dry-run by default.** Nothing is enabled or moved without ``--execute``.
* **Explicit execution.** With ``--execute`` the normal entry asks for ``MOVE``;
  the one-click launcher may set ``AIPC_RESET_AUTO_START=1`` after its countdown.
* **Corridor movement.** The commanded position is monotonically stepped from
  the measured start toward the target and can never leave the
  start-to-target corridor.
* **Tracking watchdog.** If the arm stops tracking its own commands
  (stalled against an obstacle), the script aborts.
* **No disable command.** Exit reports the cached driver enable state and
  sends holding targets only to enabled arms. Physical holding is not
  guaranteed; support the arms before any later power-off.
* **Ctrl+C / SIGTERM** stop the motion and hold the measured pose.
"""

from __future__ import annotations

import argparse
import os
import signal
import time

import numpy as np

from piper_towel_folding.piper.dual_robot import PIPERDual, PIPERDualConfig
from piper_towel_folding.safety import TRAINING_START_ACTION

# Canonical training start pose = frame-0 action of the reference dataset
# (see safety.TRAINING_START_ACTION / lerobot_eval.py's "Dataset frame 0
# action"). This is the SAME pose that safety.validate_start_pose gates
# against, so resetting here guarantees the eval start gate passes.
# The previous "median across demos" start differed from frame-0 by up to
# ~0.2 rad on several joints (left j6 0.175, right j4 0.202, right j6 0.164)
# and could trip the 0.15 rad gate even after a correct reset — it was
# removed so reset and eval cannot disagree.
TRAINING_START_POSE = TRAINING_START_ACTION.astype(np.float64)

# Nominal command envelope for the target pose.
SINGLE_ARM_LOWER = np.asarray([-1.61, -0.05, -1.93, -1.58, -1.40, -1.58, 0.0])
SINGLE_ARM_UPPER = np.asarray([1.61, 2.10, 0.06, 1.58, 1.40, 1.58, 0.08])
LOWER_LIMITS = np.concatenate((SINGLE_ARM_LOWER, SINGLE_ARM_LOWER))
UPPER_LIMITS = np.concatenate((SINGLE_ARM_UPPER, SINGLE_ARM_UPPER))

# A fallen arm can legitimately report a pose outside the nominal envelope,
# so the recovery START is validated against a broad single-turn sanity
# envelope instead. Generated commands still move component-wise from the
# measured start toward the target and can never go farther outward.
RECOVERY_LOWER_LIMITS = np.concatenate((np.asarray([-3.20] * 6 + [-0.01]), np.asarray([-3.20] * 6 + [-0.01])))
RECOVERY_UPPER_LIMITS = np.concatenate((np.asarray([3.20] * 6 + [0.10]), np.asarray([3.20] * 6 + [0.10])))

JOINT_INDICES = np.asarray([0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12])
GRIPPER_INDICES = np.asarray([6, 13])
POSITION_KEYS = [f"joint_{i}_pos" for i in range(1, 7)] + ["gripper_pos"]

stop_requested = False
awaiting_move_confirmation = False


def request_stop(_signum=None, _frame=None) -> None:
    global stop_requested
    stop_requested = True
    # input() can otherwise continue waiting after a handled SIGINT.
    if awaiting_move_confirmation:
        raise KeyboardInterrupt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Slowly move dual Piper followers to the demonstrated start pose without disabling."
    )
    parser.add_argument("--left-can", default="can1")
    parser.add_argument("--right-can", default="can0")
    parser.add_argument(
        "--arm",
        required=True,
        choices=("left", "right", "both"),
        help="Move the left arm, right arm, or both arms toward the target.",
    )
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--joint-speed", type=float, default=0.06, help="Maximum joint speed in rad/s.")
    parser.add_argument("--gripper-speed", type=float, default=0.006, help="Maximum gripper speed in m/s.")
    parser.add_argument("--tracking-limit", type=float, default=0.12, help="Maximum joint tracking error in rad.")
    parser.add_argument("--gripper-tracking-limit", type=float, default=0.015, help="Maximum gripper tracking error in m.")
    parser.add_argument("--settle-tolerance", type=float, default=0.025, help="Final joint tolerance in rad.")
    parser.add_argument("--settle-timeout", type=float, default=10.0)
    parser.add_argument("--feedback-warmup", type=int, default=30)
    parser.add_argument(
        "--target",
        type=float,
        nargs=14,
        default=None,
        metavar=("LJ1", "LJ2", "LJ3", "LJ4", "LJ5", "LJ6", "LG", "RJ1", "RJ2", "RJ3", "RJ4", "RJ5", "RJ6", "RG"),
        help="Optional explicit 14-value target. Default is the training frame-0 start pose.",
    )
    parser.add_argument("--execute", action="store_true", help="Actually move the robot; default is dry-run.")
    return parser.parse_args()


def read_bus_positions(bus) -> np.ndarray:
    state = bus.read()
    missing = [key for key in POSITION_KEYS if key not in state]
    if missing:
        raise RuntimeError(f"Missing Piper feedback keys: {missing}")
    values = np.asarray([state[key] for key in POSITION_KEYS], dtype=np.float64)
    if values.shape != (7,) or not np.isfinite(values).all():
        raise RuntimeError(f"Invalid Piper feedback: {values}")
    return values


def read_positions(robot: PIPERDual) -> np.ndarray:
    return np.concatenate((read_bus_positions(robot.left_bus), read_bus_positions(robot.right_bus)))


def read_stable_positions(robot: PIPERDual, reads: int) -> np.ndarray:
    positions = None
    for _ in range(max(1, reads)):
        positions = read_positions(robot)
        time.sleep(0.05)
    if positions is None:
        raise RuntimeError("No Piper feedback received.")
    return positions


def validate_pose(pose: np.ndarray, name: str, allow_recovery: bool = False) -> None:
    if pose.shape != (14,) or not np.isfinite(pose).all():
        raise ValueError(f"{name} must contain 14 finite values, got {pose}")
    lower = RECOVERY_LOWER_LIMITS if allow_recovery else LOWER_LIMITS
    upper = RECOVERY_UPPER_LIMITS if allow_recovery else UPPER_LIMITS
    bad = np.flatnonzero((pose < lower) | (pose > upper))
    if bad.size:
        details = [
            f"index={i}, value={pose[i]:.6f}, range=[{lower[i]:.6f}, {upper[i]:.6f}]"
            for i in bad
        ]
        raise ValueError(f"{name} violates configured limits: " + "; ".join(details))


def arm_is_enabled(bus) -> bool:
    info = bus.piper.GetArmLowSpdInfoMsgs()
    motors = (info.motor_1, info.motor_2, info.motor_3, info.motor_4, info.motor_5, info.motor_6)
    return all(bool(motor.foc_status.driver_enable_status) for motor in motors)


def driver_snapshot(robot):
    result = {}
    for name, bus in (("left", robot.left_bus), ("right", robot.right_bus)):
        info = bus.piper.GetArmLowSpdInfoMsgs()
        motors = [getattr(info, "motor_"+str(i)) for i in range(1, 7)]
        result[name] = dict(
            sdk_receipt_timestamp=getattr(info, "time_stamp", None),
            enabled=[bool(m.foc_status.driver_enable_status) for m in motors],
            fault_bits=[hex(m.foc_status_code & 0xBF) for m in motors],
            voltage_V=[m.vol / 10.0 for m in motors])
    return result


def enable_both_holding(robot, hold, timeout=8.0):
    """Official EnablePiper polling, with BOTH measured targets refreshed each iteration.

    This runs only after MOVE; never used to re-enable after a movement fault.
    EnablePiper returns previously received feedback, not a synchronous ACK.
    """
    import json
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stop_requested:
            raise KeyboardInterrupt
        left_ready = robot.left_bus.piper.EnablePiper()
        right_ready = robot.right_bus.piper.EnablePiper()
        send_pose(robot, hold)
        if left_ready and right_ready and arm_is_enabled(robot.left_bus) and arm_is_enabled(robot.right_bus):
            print("RESET_ENABLE_FEEDBACK="+json.dumps(driver_snapshot(robot)), flush=True)
            print("Both arms report enabled; measured targets were sent. Beginning reference interpolation.", flush=True)
            return
        time.sleep(0.01)
    raise RuntimeError("Timed out enabling arms. No disable or protection-reset command was sent.")


def send_pose(robot: PIPERDual, pose: np.ndarray) -> None:
    validate_pose(pose, "recovery command", allow_recovery=True)
    robot.left_bus.write(pose[:7].tolist())
    robot.right_bus.write(pose[7:].tolist())


def hold_measured_pose(robot):
    """Preserve reference hold on enabled arms; never claim an unverified physical hold."""
    import json
    try:
        pose = read_stable_positions(robot, 3)
        pose[GRIPPER_INDICES] = np.clip(pose[GRIPPER_INDICES], 0.0, 0.08)
        validate_pose(pose, "exit holding feedback", allow_recovery=True)
        snapshot = driver_snapshot(robot)
        print("RESET_EXIT_DRIVER_FEEDBACK="+json.dumps(snapshot), flush=True)
        for name, bus, arm_pose in (("left", robot.left_bus, pose[:7]), ("right", robot.right_bus, pose[7:])):
            if all(snapshot[name]["enabled"]):
                bus.write(arm_pose.tolist())
                print(name+": measured-pose target sent; physical holding is not independently verified.", flush=True)
            else:
                print("WARNING: "+name+" drivers do not all report enabled. No automatic re-enable; physically support the arm.", flush=True)
        time.sleep(0.3)
    except Exception as error:
        print("WARNING: exit hold could not be confirmed: "+str(error), file=__import__("sys").stderr)


def move_slowly(robot: PIPERDual, start: np.ndarray, target: np.ndarray, args: argparse.Namespace) -> None:
    period = 1.0 / args.fps
    joint_step = args.joint_speed / args.fps
    gripper_step = args.gripper_speed / args.fps
    command = start.copy()
    corridor_lower = np.minimum(start, target) - 1e-9
    corridor_upper = np.maximum(start, target) + 1e-9
    step = 0

    while not stop_requested:
        measured = read_positions(robot)
        if not arm_is_enabled(robot.left_bus) or not arm_is_enabled(robot.right_bus):
            import json
            raise RuntimeError("Driver enable lost during reference reset; no automatic re-enable. "
                               +json.dumps(driver_snapshot(robot)))
        joint_tracking = float(np.max(np.abs(measured[JOINT_INDICES] - command[JOINT_INDICES])))
        gripper_tracking = float(np.max(np.abs(measured[GRIPPER_INDICES] - command[GRIPPER_INDICES])))
        if joint_tracking > args.tracking_limit:
            raise RuntimeError(
                f"Safety stop: joint tracking error {joint_tracking:.6f} rad exceeds {args.tracking_limit:.6f} rad; "
                f"step={step} measured={measured.tolist()} previous_command={command.tolist()}"
            )
        if gripper_tracking > args.gripper_tracking_limit:
            raise RuntimeError(
                f"Safety stop: gripper tracking error {gripper_tracking:.6f} m exceeds "
                f"{args.gripper_tracking_limit:.6f} m."
            )

        delta = target - command
        next_command = command.copy()
        next_command[JOINT_INDICES] += np.clip(delta[JOINT_INDICES], -joint_step, joint_step)
        next_command[GRIPPER_INDICES] += np.clip(delta[GRIPPER_INDICES], -gripper_step, gripper_step)
        if np.any(next_command < corridor_lower) or np.any(next_command > corridor_upper):
            raise RuntimeError("Internal safety error: recovery command left the start-to-target corridor.")
        if stop_requested:
            break
        send_pose(robot, next_command)
        command = next_command

        if step % max(1, int(args.fps)) == 0:
            remaining = float(np.max(np.abs(target[JOINT_INDICES] - command[JOINT_INDICES])))
            print(f"step={step:05d} max_remaining={remaining:.5f} rad tracking={joint_tracking:.5f} rad")

        joints_done = np.all(np.abs(target[JOINT_INDICES] - command[JOINT_INDICES]) <= 1e-8)
        grippers_done = np.all(np.abs(target[GRIPPER_INDICES] - command[GRIPPER_INDICES]) <= 1e-8)
        if joints_done and grippers_done:
            break
        step += 1
        time.sleep(period)

    if stop_requested:
        print("Stop requested during movement.")
        return

    print("Target command reached; waiting for the arms to settle...")
    deadline = time.monotonic() + args.settle_timeout
    while time.monotonic() < deadline and not stop_requested:
        measured = read_positions(robot)
        if not arm_is_enabled(robot.left_bus) or not arm_is_enabled(robot.right_bus):
            import json
            raise RuntimeError("Driver enable lost while settling; no automatic re-enable. "
                               +json.dumps(driver_snapshot(robot)))
        joint_error = float(np.max(np.abs(measured[JOINT_INDICES] - target[JOINT_INDICES])))
        gripper_error = float(np.max(np.abs(measured[GRIPPER_INDICES] - target[GRIPPER_INDICES])))
        if stop_requested:
            break
        send_pose(robot, target)
        if joint_error <= args.settle_tolerance and gripper_error <= args.gripper_tracking_limit:
            print(f"Pose reset complete. Maximum joint error: {joint_error:.6f} rad")
            return
        time.sleep(period)
    if not stop_requested:
        raise RuntimeError("Target did not settle before timeout.")


def main() -> None:
    global awaiting_move_confirmation
    args = parse_args()
    if args.fps <= 0 or args.joint_speed <= 0 or args.gripper_speed <= 0:
        raise ValueError("--fps, --joint-speed and --gripper-speed must be positive.")

    requested_target = TRAINING_START_POSE.copy() if args.target is None else np.asarray(args.target, dtype=np.float64)
    validate_pose(requested_target, "target")
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    robot = None
    enabled = False
    try:
        robot = PIPERDual(PIPERDualConfig(left_port=args.left_can, right_port=args.right_can, read_only=True, cameras={}))
        # We enable arms ourselves below and never command the zero pose, so
        # skip the wrapper's enable path.
        robot._is_connected = True
        print("Warming up Piper feedback...")
        current = read_stable_positions(robot, args.feedback_warmup)
        validate_pose(current, "current feedback", allow_recovery=True)

        target = requested_target.copy()
        if args.arm == "left":
            target[7:] = current[7:]
        elif args.arm == "right":
            target[:7] = current[:7]
        validate_pose(target, "recovery target", allow_recovery=True)

        print("Current:", np.array2string(current, precision=6, separator=", "))
        print("Target: ", np.array2string(target, precision=6, separator=", "))
        print("Delta:  ", np.array2string(target - current, precision=6, separator=", "))

        if not args.execute:
            print("DRY-RUN only. Re-run with --execute to move slowly to this target.")
            return

        if stop_requested:
            raise KeyboardInterrupt
        if os.environ.get("AIPC_RESET_AUTO_START") == "1":
            print("RESET_AUTO_START: one-click task requested actual reset.", flush=True)
            answer = "MOVE"
        else:
            awaiting_move_confirmation = True
            try:
                answer = input("Support the arms, clear the workspace, hold the emergency stop, and type MOVE to continue: ").strip()
            finally:
                awaiting_move_confirmation = False
        if stop_requested:
            raise KeyboardInterrupt
        if answer != "MOVE":
            print("Cancelled. Arms were not enabled or moved by this script.")
            raise SystemExit(2)

        # The operator may have supported/repositioned the disabled arm while answering MOVE.
        # Refresh the holding start now; the explicitly selected task target is unchanged.
        current = read_positions(robot)
        if args.arm == "left":
            target[7:] = current[7:]
        elif args.arm == "right":
            target[:7] = current[:7]
        print("RESET_START_VERSION=2026-10-14-native-enable-polling", flush=True)
        print("Holding start after MOVE:", np.array2string(current, precision=6, separator=", "), flush=True)
        print("Preloading measured targets; polling native enable for both arms...")
        if stop_requested:
            raise KeyboardInterrupt
        enabled = True  # Track control attempts so partial preload/enable also gets truthful exit handling.
        send_pose(robot, current)
        enable_both_holding(robot, current)
        print("Moving slowly. Press Ctrl+C to stop and hold the measured pose.")
        move_slowly(robot, current, target, args)
        if stop_requested:
            raise KeyboardInterrupt
        print("RESET_EXECUTION_COMPLETE", flush=True)
    except KeyboardInterrupt:
        print("Keyboard interrupt received; no following task will be started.")
        raise SystemExit(130)
    except Exception as error:
        print(f"FATAL: {type(error).__name__}: {error}", file=__import__("sys").stderr)
        raise
    finally:
        if robot is not None and enabled:
            hold_measured_pose(robot)
            print("No disable/protection-reset command sent; use reported driver state, not this log, to judge enable. Physically support before power-off.")


if __name__ == "__main__":
    main()
