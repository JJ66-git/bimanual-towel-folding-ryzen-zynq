"""Versioned little-endian request/response frames; no robot imports."""
from dataclasses import dataclass
import math
import struct
import zlib

MAGIC = b'FPG1'
HEADER = struct.Struct('<4sBBHIIQ')
REQUEST = struct.Struct('<IIIQII84i')
RESPONSE = struct.Struct('<IIIII')
CRC = struct.Struct('<I')
UNKNOWN = 0xFFFFFFFF
CHECK_START, CHECK_CORRECTION, RUNNING, MANUAL_HANDOFF = 1, 2, 4, 8
ACT, BLEND = 3, 4


def quantize(values):
    if len(values) != 14:
        raise ValueError('Expected 14 values')
    encoded = []
    for value in values:
        value = float(value)
        if not math.isfinite(value):
            raise ValueError('Non-finite motion value')
        scaled = math.floor(abs(value) * 1_000_000 + 0.5)
        integer = -scaled if value < 0 else scaled
        if not -(1 << 31) <= integer < (1 << 31):
            raise ValueError('Motion value exceeds signed int32')
        encoded.append(integer)
    return tuple(encoded)


@dataclass(frozen=True)
class Sample:
    profile_crc: int
    step: int
    flags: int
    elapsed_us: int
    feedback_age_us: int
    queue_depth: int
    target: tuple
    measured: tuple
    previous: tuple
    act: tuple
    correction: tuple
    previous_correction: tuple

    def pack(self):
        vectors = (self.target, self.measured, self.previous, self.act,
                   self.correction, self.previous_correction)
        if any(len(vector) != 14 for vector in vectors):
            raise ValueError('Every vector must contain 14 values')
        if self.flags & ~15:
            raise ValueError('Unknown request flags')
        return REQUEST.pack(self.profile_crc, self.step, self.flags, self.elapsed_us,
                            self.feedback_age_us, self.queue_depth,
                            *(value for vector in vectors for value in vector))

    @classmethod
    def unpack(cls, payload):
        if len(payload) != REQUEST.size:
            raise ValueError('Invalid request length')
        values = REQUEST.unpack(payload)
        result = cls(*values[:6], *(tuple(values[i:i+14]) for i in range(6, 90, 14)))
        if result.flags & ~15:
            raise ValueError('Unknown request flags')
        return result


def frame(message_type, session, sequence, tick_us, payload):
    expected = {1: REQUEST.size, 2: RESPONSE.size}.get(message_type)
    if expected is None or len(payload) != expected:
        raise ValueError('Invalid message type/length')
    body = HEADER.pack(MAGIC, 1, message_type, len(payload), session, sequence, tick_us) + payload
    return body + CRC.pack(zlib.crc32(body) & UNKNOWN)


def unframe(data):
    if len(data) < HEADER.size + CRC.size:
        raise ValueError('Truncated frame')
    magic, version, kind, length, session, sequence, tick_us = HEADER.unpack_from(data)
    if magic != MAGIC or version != 1 or kind not in (1, 2):
        raise ValueError('Invalid frame header')
    if length != {1: REQUEST.size, 2: RESPONSE.size}[kind]:
        raise ValueError('Unexpected payload length')
    if len(data) != HEADER.size + length + CRC.size:
        raise ValueError('Invalid frame size')
    if zlib.crc32(data[:-4]) & UNKNOWN != CRC.unpack_from(data, len(data)-4)[0]:
        raise ValueError('CRC mismatch')
    return kind, session, sequence, tick_us, data[HEADER.size:-4]


class FrameStream:
    """Recover complete frames after noise, truncation or a corrupt CRC."""
    def __init__(self):
        self.buffer = bytearray()
        self.errors = 0

    def feed(self, data):
        self.buffer.extend(data)
        results = []
        while True:
            offset = self.buffer.find(MAGIC)
            if offset < 0:
                self.buffer[:] = self.buffer[-3:]
                break
            if offset:
                del self.buffer[:offset]
            if len(self.buffer) < HEADER.size:
                break
            _, version, kind, length, *_ = HEADER.unpack_from(self.buffer)
            if version != 1 or kind not in (1, 2) or length != {1: REQUEST.size, 2: RESPONSE.size}.get(kind):
                self.errors += 1
                del self.buffer[0]
                continue
            size = HEADER.size + length + CRC.size
            if len(self.buffer) < size:
                break
            packet = bytes(self.buffer[:size])
            try:
                unframe(packet)
            except ValueError:
                self.errors += 1
                del self.buffer[0]
                continue
            results.append(packet)
            del self.buffer[:size]
        return results
