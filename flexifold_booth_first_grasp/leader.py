"""Receive Piper V2 leader commands on an isolated Linux SocketCAN interface."""
from pathlib import Path
import socket
import struct
import threading
import time

JOINTS = (0x155, 0x156, 0x157)
GRIPPER = 0x159
COMMANDS = (*JOINTS, GRIPPER)
CAN_FRAME = struct.Struct("=IB3x8s")
STANDARD_DATA_MASK = 0xE00007FF
MAX_AGE = 0.2


def isolated_leader_interface(site, net_root=Path('/sys/class/net')):
    sides = ('left', 'right')
    follower_ports = {site['robot'][side + '_port'] for side in sides}
    follower_serials = {site['usb_serials'][side] for side in sides}
    if len(follower_serials) != 2 or any(not serial or serial.startswith('REPLACE_') for serial in follower_serials):
        raise RuntimeError('请先确认左右执行臂的 USB 绑定，再识别主臂。')

    candidates = []
    for interface in net_root.iterdir():
        if interface.name in follower_ports:
            continue
        try:
            if (interface / 'type').read_text().strip() != '280':
                continue
            device = (interface / 'device').resolve(strict=True)
            serial_path = next((parent / 'serial' for parent in (device, *device.parents)
                                if (parent / 'serial').is_file()), None)
            if serial_path is None:
                continue
            serial = serial_path.read_text().strip()
        except OSError:
            continue  # The adapter may disappear during a hot unplug.
        if serial and serial not in follower_serials:
            candidates.append(interface)

    if not candidates:
        raise RuntimeError('未识别到独立主臂 USB-CAN。请接好主臂的 USB-CAN 后重试。')
    if len(candidates) > 1:
        raise RuntimeError('检测到多个独立 USB-CAN。请只保留主臂使用的独立适配器后重试。')
    interface = candidates[0]
    if not int((interface / 'flags').read_text().strip(), 0) & 1:
        raise RuntimeError(f'已识别主臂接口 {interface.name}，请工作人员启用 1 Mbps CAN 后重试。')
    return interface.name


class Leader:
    def __init__(self, interface):
        self.interface = interface
        self.latest = {}
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.error = None
        self.sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        try:
            self.sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER, b"".join(
                struct.pack("=II", command, STANDARD_DATA_MASK) for command in COMMANDS
            ))
            self.sock.settimeout(0.15)
            self.sock.bind((interface,))
        except BaseException:
            self.sock.close()
            raise
        self.worker = threading.Thread(target=self.listen, daemon=True)
        self.worker.start()

    def listen(self):
        while not self.closed.is_set():
            try:
                packet, _, flags, _ = self.sock.recvmsg(CAN_FRAME.size)
            except socket.timeout:
                continue
            except OSError:
                self.error = "右主臂 CAN 接口中断，请检查连接并重新载入互动。"
                return
            # SocketCAN marks frames from any socket on this host with MSG_DONTROUTE.
            if flags & (socket.MSG_DONTROUTE | socket.MSG_TRUNC) or len(packet) != CAN_FRAME.size:
                continue
            can_id, length, payload = CAN_FRAME.unpack(packet)
            if can_id in COMMANDS and length == 8:
                values = struct.unpack(">ii", payload) if can_id in JOINTS else (struct.unpack_from(">i", payload)[0],)
                with self.lock:
                    self.latest[can_id] = (time.monotonic(), values)

    def snapshot(self, since=0):
        with self.lock:
            now = time.monotonic()
            fresh = max(since, now - MAX_AGE)
            if self.error or any(self.latest.get(command, (0,))[0] < fresh for command in COMMANDS):
                return None
            return dict(
                joint_deg=[value / 1000 for command in JOINTS for value in self.latest[command][1]],
                gripper_mm=self.latest[GRIPPER][1][0] / 1000,
                component_age_ms={hex(command):round((now-self.latest[command][0])*1000, 3) for command in COMMANDS},
            )

    def status(self, wait_seconds=0):
        deadline = time.monotonic() + wait_seconds
        sample = self.snapshot()
        while sample is None and not self.error:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self.closed.wait(min(remaining, 0.02)):
                break
            sample = self.snapshot()
        return dict(ready=sample is not None, sample=sample, error=self.error)

    def close(self):
        self.closed.set()
        self.worker.join()
        self.sock.close()
