"""Read-only PYNQ Overlay smoke test for the motion guard PL registers."""

from pathlib import Path
import time

from pynq import Overlay
from board.ownership import exclusive_owner


ROOT = Path(__file__).resolve().parents[1]
PACKAGED_BITFILE = ROOT / "overlay" / "own_hybrid_motion_guard_watchdog.bit"
LOCAL_BITFILE = Path(__file__).resolve().with_name("own_hybrid_motion_guard_watchdog.bit")
BITFILE = PACKAGED_BITFILE if PACKAGED_BITFILE.exists() else LOCAL_BITFILE
BUILD_ID = 0x46504732
PROFILE_CRC = 0x8F4C7F55


@exclusive_owner()
def run():
    overlay = Overlay(str(BITFILE))
    print("IPs:", list(overlay.ip_dict))
    guard = overlay.guard
    build_id = guard.read(0x00)
    if build_id != BUILD_ID:
        raise RuntimeError("Unexpected motion guard BUILD_ID: 0x{:08x}".format(build_id))

    for offset in (0x10, 0x14, 0x18, 0x1C, 0x20):
        guard.write(offset, 0)
    guard.write(0x10, PROFILE_CRC)
    guard.write(0x18, 0x4)
    guard.write(0x1C, 0xFFFFFFFF)
    for base in (0x100, 0x140, 0x180, 0x1C0, 0x200, 0x240):
        for index in range(14):
            value = 1610002 if base == 0x100 and index == 0 else 0
            guard.write(base + 4 * index, value)

    guard.write(0x04, 1)
    for _ in range(1000):
        status = guard.read(0x08)
        if status & 0x2 and not status & 0x1:
            break
        time.sleep(0.001)
    else:
        raise RuntimeError("Motion guard PL decision timed out; STATUS=0x{:08x}".format(status))

    result = (guard.read(0x40), guard.read(0x44), guard.read(0x48))
    expected = (0x5, 0x8, 0x3)
    if result != expected:
        raise RuntimeError("PL result {} does not match expected {}".format(
            tuple(hex(value) for value in result),
            tuple(hex(value) for value in expected)))
    print("BUILD_ID=0x{:08x} result={}".format(
        build_id, tuple(hex(value) for value in result)))
    print("PASS: PYNQ PS -> AXI4-Lite -> motion_guard register smoke test")


if __name__ == "__main__":
    run()
