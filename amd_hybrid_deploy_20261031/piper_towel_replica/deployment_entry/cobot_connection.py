"""Local policy connection: native enable + measured-pose hold, never close to zero.

No model/filter/limit changes, transport hooks, error clearing or zeroing.
Only SDK cached getters are used for independent gripper telemetry.
"""
import json
import math
import time

POSITION_KEYS = [f'joint_{i}_pos' for i in range(1, 7)] + ['gripper_pos']


def gripper_snapshot(robot):
    """Copy gripper and arm cached state; never add a control decision.

    The SDK may apply its own limits to decoded width. These are NOT raw CAN
    bytes, hardware exposure times, command acknowledgements or a new guard.
    Missing telemetry is reported; it does not clear faults or change targets.
    """
    result = {}
    for side in ('left', 'right'):
        row = dict(sampled_monotonic_s=time.perf_counter(), source='SDK cached gripper feedback')
        try:
            msg = getattr(robot, side+'_bus').piper.GetArmGripperMsgs()
            state = msg.gripper_state
            width = int(state.grippers_angle)
            effort = int(state.grippers_effort)
            status = int(state.status_code)
            row.update(sdk_receipt_timestamp=float(msg.time_stamp), sdk_hz=float(msg.Hz),
                       width_um=width, width_mm=width/1000., effort_Nm=effort/1000.,
                       status_code=hex(status), enabled=bool(status & 0x40),
                       fault_bits=hex(status & 0x3F), zeroed=bool(status & 0x80))
            piper = getattr(robot, side+'_bus').piper
            arm = piper.GetArmStatus()
            joints = piper.GetArmLowSpdInfoMsgs()
            row.update(arm_ctrl_mode=str(arm.arm_status.ctrl_mode),
                       arm_status=str(arm.arm_status.arm_status),
                       arm_receipt_timestamp=float(arm.time_stamp),
                       joint_receipt_timestamp=float(joints.time_stamp),
                       joint_enabled=[bool(getattr(joints, 'motor_'+str(i))
                                           .foc_status.driver_enable_status)
                                      for i in range(1,7)])
        except Exception as error:
            row['error'] = f'{type(error).__name__}: {error}'
        result[side] = row
    return result


def _measured_pose(robot, deadline, stop_requested):
    """Wait only for initial feedback, rather than send SDK default zeros."""
    while time.monotonic() < deadline:
        if stop_requested():
            raise KeyboardInterrupt
        pose = []
        ready = True
        for bus in (robot.left_bus, robot.right_bus):
            if bus.piper.GetArmJointMsgs().time_stamp <= 0 or bus.piper.GetArmGripperMsgs().time_stamp <= 0:
                ready = False
                break
            state = bus.read()
            pose.extend(float(state[key]) for key in POSITION_KEYS)
        if ready:
            if not all(math.isfinite(value) for value in pose):
                raise RuntimeError('Non-finite measured policy connection pose; no holding target sent')
            return pose
        time.sleep(.01)
    raise RuntimeError('No initial joint/gripper feedback for policy connection; no holding target sent')


def _send_hold(robot, pose):
    # Keep the reference bus.write path and its units, 50% ratio, effort=1000,
    # enable=0x01 (NOT 0x03 clear+enable), set_zero=0, and SDK limits unchanged.
    robot.left_bus.write(pose[:7])
    robot.right_bus.write(pose[7:])


def connect_holding_measured(robot, *, stop_requested=lambda: False, timeout=8.0):
    """Connect reference cameras passively, then enable while holding measured pose.

    PIPERDual's active connect calls bus.connect(), which sends GripperCtrl(0).
    Use its existing passive connection first; native polling then matches the
    working local reset's enable+hold sequence. No polling after policy starts,
    no gripper enable/fault/zeroed gate, and no automatic recovery.
    """
    read_only = robot.config.read_only
    report = dict(version='2026-10-15-measured-policy-connect', execute=not read_only,
                  controller_commands_sent=False, hold_target=None)
    robot.config.read_only = True
    try:
        robot.connect()
        if stop_requested():
            raise KeyboardInterrupt
        report['gripper_before'] = gripper_snapshot(robot)
        print('POLICY_CONNECTION_BEFORE='+json.dumps(report['gripper_before']), flush=True)
        if not read_only:
            deadline = time.monotonic() + timeout
            pose = _measured_pose(robot, deadline, stop_requested)
            report['hold_target'] = pose
            print('POLICY_CONNECTION_HOLD='+json.dumps(pose), flush=True)
            # Preload BOTH measured targets, then refresh BOTH on every poll.
            report['controller_commands_sent'] = True
            _send_hold(robot, pose)
            while time.monotonic() < deadline:
                if stop_requested():
                    raise KeyboardInterrupt
                left_ready = robot.left_bus.piper.EnablePiper()
                right_ready = robot.right_bus.piper.EnablePiper()
                _send_hold(robot, pose)
                if left_ready and right_ready:
                    break
                time.sleep(.01)
            else:
                raise RuntimeError('Timed out enabling policy connection; no disable/clear/zero command sent')
        report['gripper_after'] = gripper_snapshot(robot)
        print('POLICY_CONNECTION_AFTER='+json.dumps(report), flush=True)
        return report
    except BaseException:
        # Partial camera startup/enable must not leak cameras or mask the cause.
        # Never auto-clear, re-enable, disable or send a different target here.
        print('POLICY_CONNECTION_ABORT_GRIPPERS='+json.dumps(gripper_snapshot(robot)), flush=True)
        for camera in robot.cameras.values():
            try:
                if camera.is_connected:
                    camera.disconnect()
            except Exception as error:
                print('WARNING: camera cleanup after connection failure: '+str(error), flush=True)
        robot._is_connected = False
        raise
    finally:
        robot.config.read_only = read_only
