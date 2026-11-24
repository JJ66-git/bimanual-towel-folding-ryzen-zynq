"""Frozen FPG1 v3 and FVE1 v1 wire protocol. No hardware imports."""
from dataclasses import dataclass
import struct
import zlib

MAGIC_FPG1 = b"FPG1"
MAGIC_FVE1 = b"FVE1"
HEADER = struct.Struct("<4sBBHIIQ")
CRC = struct.Struct("<I")
FPG1_REQUEST = struct.Struct("<IIIQII84i")
FPG1_REPLY = struct.Struct("<10I")
FVE1_FEATURE = struct.Struct("<QI6H")
FVE1_RESULT = struct.Struct("<4I")
UNKNOWN = 0xFFFFFFFF
BUILD_ID = 0x46504735
PHASE_FLAGS_MASK = 0x30F


@dataclass(frozen=True)
class Frame:
    magic: bytes
    version: int
    kind: int
    session: int
    sequence: int
    tick_us: int
    values: tuple


def encode(magic, version, kind, session, sequence, tick_us, values):
    layouts = {
        (MAGIC_FPG1, 3, 1): FPG1_REQUEST,
        (MAGIC_FPG1, 3, 2): FPG1_REPLY,
        (MAGIC_FVE1, 1, 1): FVE1_FEATURE,
        (MAGIC_FVE1, 1, 2): FVE1_RESULT,
        (MAGIC_FVE1, 1, 3): struct.Struct("<"),
        (MAGIC_FVE1, 1, 4): FVE1_RESULT,
    }
    layout = layouts.get((magic, version, kind))
    if layout is None or len(values) != len(layout.unpack(bytes(layout.size))):
        raise ValueError("unsupported_frame")
    if not 1 <= session <= 0xFFFFFFFE or sequence == UNKNOWN:
        raise ValueError("bad_identity")
    payload = layout.pack(*values)
    body = HEADER.pack(magic, version, kind, len(payload), session, sequence, tick_us) + payload
    return body + CRC.pack(zlib.crc32(body) & UNKNOWN)


def decode(packet, expected_build_id=BUILD_ID):
    if len(packet) < HEADER.size + CRC.size:
        raise ValueError("short_frame")
    magic, version, kind, length, session, sequence, tick_us = HEADER.unpack_from(packet)
    layouts = {
        (MAGIC_FPG1, 3, 1): FPG1_REQUEST,
        (MAGIC_FPG1, 3, 2): FPG1_REPLY,
        (MAGIC_FVE1, 1, 1): FVE1_FEATURE,
        (MAGIC_FVE1, 1, 2): FVE1_RESULT,
        (MAGIC_FVE1, 1, 3): struct.Struct("<"),
        (MAGIC_FVE1, 1, 4): FVE1_RESULT,
    }
    layout = layouts.get((magic, version, kind))
    if layout is None:
        raise ValueError("unsupported_header")
    if length != layout.size or len(packet) != HEADER.size + length + CRC.size:
        raise ValueError("bad_length")
    if CRC.unpack_from(packet, len(packet) - CRC.size)[0] != zlib.crc32(packet[:-4]) & UNKNOWN:
        raise ValueError("bad_crc")
    if not 1 <= session <= 0xFFFFFFFE or sequence == UNKNOWN:
        raise ValueError("bad_identity")
    values = layout.unpack_from(packet, HEADER.size)
    if (magic, version, kind) == (MAGIC_FPG1, 3, 1):
        profile, step, flags, elapsed, age, depth = values[:6]
        if flags & ~15:
            raise ValueError("reserved_request_flags")
        if step == UNKNOWN:
            if flags or elapsed or sequence:
                raise ValueError("bad_probe")
        elif not flags & 4 or sequence == 0:
            raise ValueError("bad_task")
    elif (magic, version, kind) == (MAGIC_FPG1, 3, 2):
        _, violations, events, scheduled, board_us, stage, flags, cycles, build, event = values
        if violations & ~0x7FF or events & ~0xF or flags & ~PHASE_FLAGS_MASK:
            raise ValueError("reserved_response_bits")
        if scheduled not in (0, 3, 4) or stage > 3 or board_us == UNKNOWN or cycles == UNKNOWN or build != expected_build_id:
            raise ValueError("bad_result_identity")
        if (flags & 8 and not flags & 4) or (flags & 1 and not ((flags & 12) == 12 and stage == 3)):
            raise ValueError("inconsistent_stage_flags")
        pending, verify = bool(flags & 2), (flags >> 8) & 3
        if pending != (verify == 1) or pending != (event != UNKNOWN):
            raise ValueError("inconsistent_event_flags")
    elif magic == MAGIC_FVE1 and kind == 1:
        if values[-1] & ~1 or values[1] == UNKNOWN:
            raise ValueError("bad_feature")
    elif magic == MAGIC_FVE1 and kind in (2, 4):
        _, state, reason, count = values
        if state > 3 or reason > 9 or count > 255:
            raise ValueError("bad_result")
    return Frame(magic, version, kind, session, sequence, tick_us, values)
