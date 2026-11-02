"""Run the RTL golden vectors through the PYNQ motion guard Overlay."""

from pathlib import Path
import time

from pynq import Overlay
from board.ownership import exclusive_owner


PACKAGED_BITFILE = Path(__file__).resolve().parents[1] / "overlay" / "own_hybrid_motion_guard_watchdog.bit"
LOCAL_BITFILE = Path(__file__).resolve().with_name("own_hybrid_motion_guard_watchdog.bit")
BITFILE = PACKAGED_BITFILE if PACKAGED_BITFILE.exists() else LOCAL_BITFILE
VECTOR_FILE = Path(__file__).resolve().with_name("vectors.txt")
BUILD_ID = 0x46504732
PROFILE_CRC = 0x8F4C7F55
EXPECTED_VECTORS = 1678
VECTOR_BASES = (0x100, 0x140, 0x180, 0x1C0, 0x200, 0x240)


def read_vectors(path):
    lines = path.read_text(encoding="ascii").splitlines()
    if not lines:
        raise RuntimeError("Vector file is empty")
    total = int(lines[0].strip())
    if total != EXPECTED_VECTORS:
        raise RuntimeError("Expected {} reference vectors, got {}".format(
            EXPECTED_VECTORS, total))
    if len(lines) != 1 + total * 15:
        raise RuntimeError("Expected {} vectors, found {} data lines".format(
            total, len(lines) - 1))
    return lines, total


@exclusive_owner()
def run():
    lines, total = read_vectors(VECTOR_FILE)
    overlay = Overlay(str(BITFILE))
    guard = overlay.guard
    build_id = guard.read(0x00)
    if build_id != BUILD_ID:
        raise RuntimeError("Unexpected motion guard BUILD_ID: 0x{:08x}".format(build_id))

    for case_index in range(total):
        first = 1 + case_index * 15
        header = lines[first].split()
        if len(header) != 8:
            raise RuntimeError("Malformed vector header at case {}".format(case_index))
        profile_crc = int(header[0], 16)
        step = int(header[1], 10)
        flags = int(header[2], 10)
        feedback_age = int(header[3], 16)
        queue_depth = int(header[4], 16)
        expected = (int(header[5], 16), int(header[6], 16), int(header[7], 10))
        if profile_crc != PROFILE_CRC:
            raise RuntimeError("Unexpected profile CRC at case {}".format(case_index))

        vectors = [[] for _ in VECTOR_BASES]
        for dimension in range(14):
            values = lines[first + 1 + dimension].split()
            if len(values) != 6:
                raise RuntimeError("Malformed vector at case {}, dimension {}".format(
                    case_index, dimension))
            for index, value in enumerate(values):
                vectors[index].append(int(value, 10))

        for offset, value in ((0x10, profile_crc), (0x14, step), (0x18, flags),
                              (0x1C, feedback_age), (0x20, queue_depth)):
            guard.write(offset, value & 0xFFFFFFFF)
        for base, vector in zip(VECTOR_BASES, vectors):
            for index, value in enumerate(vector):
                guard.write(base + 4 * index, value & 0xFFFFFFFF)

        guard.write(0x04, 1)
        for _ in range(1000):
            status = guard.read(0x08)
            if status & 0x2 and not status & 0x1:
                break
            time.sleep(0.0001)
        else:
            raise RuntimeError("PL timeout at case {}; STATUS=0x{:08x}".format(
                case_index, status))

        actual = (guard.read(0x40), guard.read(0x44), guard.read(0x48))
        if actual != expected:
            raise RuntimeError("Case {} mismatch: got {}, expected {}".format(
                case_index,
                tuple("0x{:x}".format(value) for value in actual),
                tuple("0x{:x}".format(value) for value in expected)))
        if (case_index + 1) % 100 == 0:
            print("checked {}/{} vectors".format(case_index + 1, total), flush=True)

    print("BUILD_ID=0x{:08x}".format(build_id))
    print("PASS: {} PYNQ PL reference vectors".format(total))


if __name__ == "__main__":
    run()
