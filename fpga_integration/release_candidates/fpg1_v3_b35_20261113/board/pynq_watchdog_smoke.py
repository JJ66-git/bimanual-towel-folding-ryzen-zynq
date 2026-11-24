"""Exercise the monitor-only action watchdog through the PYNQ Overlay."""

from pathlib import Path
import time

from pynq import Overlay
from board.ownership import exclusive_owner


ROOT = Path(__file__).resolve().parents[1]
PACKAGED_BITFILE = ROOT / "overlay" / "own_hybrid_motion_guard_watchdog.bit"
LOCAL_BITFILE = Path(__file__).resolve().with_name(
    "own_hybrid_motion_guard_watchdog.bit")
BITFILE = PACKAGED_BITFILE if PACKAGED_BITFILE.exists() else LOCAL_BITFILE
BUILD_ID = 0x46504732
TIMEOUT_CYCLES = 25_000_000
NO_COMMIT_WAIT_S = 0.65
MIN_TIMEOUT_WALL_S = 0.45
DETECTION_LIMIT_S = 3.0

WD_CONTROL = 0x04C
WD_ACTION_COMMIT = 0x050
WD_STATUS = 0x054
WD_AGE_LO = 0x058
WD_AGE_HI = 0x05C
WD_TIMEOUT_COUNT = 0x060
WD_LAST_GAP_LO = 0x064
WD_LAST_GAP_HI = 0x068


def read_u64(guard, low_offset, high_offset):
    low = guard.read(low_offset)
    high = guard.read(high_offset)
    return (high << 32) | low


def wait_for_timeout(guard):
    deadline = time.monotonic() + DETECTION_LIMIT_S
    while time.monotonic() < deadline:
        status = guard.read(WD_STATUS)
        count = guard.read(WD_TIMEOUT_COUNT)
        if status & 0x4 and count == 1:
            return status
        time.sleep(0.01)
    raise RuntimeError("Watchdog timeout not observed: status=0x{:08x}, count={}".format(
        guard.read(WD_STATUS), guard.read(WD_TIMEOUT_COUNT)))


@exclusive_owner()
def run():
    overlay = Overlay(str(BITFILE))
    print("IPs:", list(overlay.ip_dict))
    guard = overlay.guard
    build_id = guard.read(0x00)
    if build_id != BUILD_ID:
        raise RuntimeError("Unexpected motion guard BUILD_ID: 0x{:08x}".format(build_id))

    guard.write(WD_CONTROL, 1)
    status = guard.read(WD_STATUS)
    if status != 1:
        raise RuntimeError("Watchdog did not enter idle PERIODIC mode: status=0x{:08x}".format(
            status))
    time.sleep(NO_COMMIT_WAIT_S)
    status = guard.read(WD_STATUS)
    count = guard.read(WD_TIMEOUT_COUNT)
    if status != 1 or count != 0:
        raise RuntimeError("Watchdog armed before first COMMIT: status=0x{:08x}, count={}".format(
            status, count))
    print("PASS: no timeout before the first accepted COMMIT")

    timeout_started = time.monotonic()
    guard.write(WD_ACTION_COMMIT, 1)
    status = guard.read(WD_STATUS)
    if status != 3:
        raise RuntimeError("First COMMIT did not arm watchdog: status=0x{:08x}".format(status))

    status = wait_for_timeout(guard)
    age = read_u64(guard, WD_AGE_LO, WD_AGE_HI)
    count = guard.read(WD_TIMEOUT_COUNT)
    elapsed = time.monotonic() - timeout_started
    if age < TIMEOUT_CYCLES or elapsed < MIN_TIMEOUT_WALL_S:
        raise RuntimeError("Watchdog expired early: age_cycles={}, elapsed_s={:.3f}".format(
            age, elapsed))
    time.sleep(0.1)
    if guard.read(WD_TIMEOUT_COUNT) != 1:
        raise RuntimeError("One quiet interval must increment timeout_count only once")
    print("PASS: timeout observed status=0x{:08x} age_cycles={} count={}".format(
        status, age, count))

    guard.write(WD_ACTION_COMMIT, 1)
    status = guard.read(WD_STATUS)
    gap = read_u64(guard, WD_LAST_GAP_LO, WD_LAST_GAP_HI)
    if status != 3 or gap < TIMEOUT_CYCLES:
        raise RuntimeError("Accepted COMMIT did not recover watchdog: status=0x{:08x}, gap={}".format(
            status, gap))
    print("PASS: accepted COMMIT cleared expired and recorded gap_cycles={}".format(gap))

    guard.write(WD_CONTROL, 0)
    status = guard.read(WD_STATUS)
    if status != 0:
        raise RuntimeError("Watchdog did not disarm: status=0x{:08x}".format(status))
    time.sleep(NO_COMMIT_WAIT_S)
    if guard.read(WD_TIMEOUT_COUNT) != 1:
        raise RuntimeError("Disabled watchdog changed timeout_count")

    print("BUILD_ID=0x{:08x}".format(build_id))
    print("PASS: PYNQ PL monitor-only watchdog smoke test")


if __name__ == "__main__":
    run()
