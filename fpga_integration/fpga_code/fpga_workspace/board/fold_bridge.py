"""Offline-testable FPG1 v3/FVE1 v1 policy and AXI adapter; no robot imports."""
import time

from host import fold_protocol as wire
from host.protocol import Sample
from host.reference import profile_crc
from host.fold_config import load as load_fold_config, identity as fold_identity

VECTOR_BASES = (0x100, 0x140, 0x180, 0x1C0, 0x200, 0x240)
FPG1_RUNNING = 1 << 2
FPG1_UNKNOWN = wire.UNKNOWN
FVE1_REASONS = {
    "WAIT_RETREAT": 1, "WRONG_EVENT": 2, "STALE_FRAME": 3,
    "DUPLICATE_FRAME": 4, "WAIT_STABLE": 5, "SHAPE_OR_OCCLUDED": 6,
    "RETREAT_LOST": 7, "EVIDENCE_TIMEOUT": 8, "WINDOW_CLOSED": 9,
}


class FoldPynqGuard:
    """Synchronous single-owner access to the frozen B35 register map."""

    def __init__(self, ip, profile, timeout=0.1, clock=time.monotonic, bulk_vectors=False):
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.ip = ip
        self.profile = profile
        self.timeout = timeout
        self.clock = clock
        self.bulk_vectors = bool(bulk_vectors and hasattr(getattr(ip,'mmio',None),'array'))
        self.guard_crc = profile_crc(profile)
        self.fold_profile = load_fold_config()
        self.fold_identity = fold_identity(self.fold_profile)
        if self.fold_profile['guard_profile_crc'] != self.guard_crc:
            raise RuntimeError('Guard profile does not match phase configuration')
        if self.read(0x98) != self.fold_identity['crc32']:
            raise RuntimeError('PL PHASE_CONFIG_CRC does not match canonical configuration')
        build_id = self.read(0x00)
        if build_id != wire.BUILD_ID:
            raise RuntimeError("Unexpected fold observer BUILD_ID: 0x{:08x}".format(build_id))

    def read(self, offset):
        return int(self.ip.read(offset)) & wire.UNKNOWN

    def write(self, offset, value):
        self.ip.write(offset, int(value) & wire.UNKNOWN)

    def _wait_idle(self):
        deadline = self.clock() + self.timeout
        while self.read(0x08) & 1:
            if self.clock() >= deadline:
                raise TimeoutError("PL busy before request")

    def _wait_done(self, started):
        deadline = self.clock() + self.timeout
        while True:
            status = self.read(0x08)
            if status & 2 and not status & 1:
                return min(wire.UNKNOWN - 1, max(0, int((self.clock() - started) * 1_000_000)))
            if self.clock() >= deadline:
                raise TimeoutError("PL result timeout; no success response is valid")

    def check(self, sample, session, sequence, reset_session=False):
        started = self.clock()
        self._wait_idle()
        if reset_session:
            self.write(0x08C, 1)
        phase_stream = sample.step != wire.UNKNOWN and bool(sample.flags & FPG1_RUNNING)
        age_valid = sample.feedback_age_us != wire.UNKNOWN and sample.feedback_age_us <= self.fold_profile['timing']['feedback_age_us']
        prevalid = (phase_stream and age_valid and sample.elapsed_us <= wire.UNKNOWN and
                    sample.step < self.profile["max_actions"])
        self.write(0x084, sample.elapsed_us & wire.UNKNOWN)
        self.write(0x088, int(prevalid) | (int(phase_stream) << 1))
        for offset, value in ((0x10, sample.profile_crc), (0x14, sample.step),
                              (0x18, sample.flags), (0x1C, sample.feedback_age_us),
                              (0x20, sample.queue_depth), (0xD4, session),
                              (0xD8, sequence)):
            self.write(offset, value)
        vectors = (sample.target, sample.measured, sample.previous, sample.act,
                   sample.correction, sample.previous_correction)
        for base, vector in zip(VECTOR_BASES, vectors):
            if self.bulk_vectors:
                import numpy as np
                words=np.asarray(vector,dtype='<i4').view('<u4')
                self.ip.mmio.array[base//4:base//4+14]=words
            else:
                for index, value in enumerate(vector):
                    self.write(base + index * 4, value)
        self.write(0x04, 1)
        board_us = self._wait_done(started)
        violations, events, scheduled = (self.read(0x40), self.read(0x44), self.read(0x48))
        phase_result = self.read(0x90)
        phase_state = phase_result & 7
        phase_flags = 0
        if phase_result & (1 << 8):
            phase_flags |= 1 << 2
        if phase_result & (1 << 9):
            phase_flags |= 1 << 3
            if phase_state == 3:
                phase_flags |= 1
        verify_word = self.read(0xE0)
        verify_state = verify_word & 0xF
        if verify_state == 1:
            phase_flags |= 1 << 1
        phase_flags |= verify_state << 8
        event_sequence = self.read(0xDC) if verify_state == 1 else wire.UNKNOWN
        cycles = self.read(0x9C)
        if cycles == wire.UNKNOWN:
            raise RuntimeError("PL phase cycle result is unavailable")
        payload = wire.FPG1_REPLY.pack(self.guard_crc, violations, events, scheduled,
            board_us, phase_state, phase_flags, cycles, self.read(0x00), event_sequence)
        return payload

    def feature(self, frame):
        self._wait_idle()
        started = self.clock()
        event_rx, frame_id, area, cx, cy, rectangularity, dark, flags = frame.values
        for offset, value in (
            (0xA0, frame.session), (0xA4, frame.sequence), (0xA8, frame_id),
            (0xAC, event_rx & wire.UNKNOWN), (0xB0, event_rx >> 32),
            (0xB4, frame.tick_us & wire.UNKNOWN), (0xB8, frame.tick_us >> 32),
            (0xBC, area | rectangularity << 16), (0xC0, cx | cy << 16),
            (0xC4, dark | flags << 16)):
            self.write(offset, value)
        self.write(0xC8, 1)
        self._wait_done(started)
        packed = self.read(0xCC)
        # B35 fixes the snapshot width: count occupies [23:16], reason [15:8].
        state, reason, count = packed & 0xF, (packed >> 8) & 0xFF, (packed >> 16) & 0xFF
        return wire.FVE1_RESULT.pack(frame_id, state, reason, count)

    def query(self, session, event_sequence):
        self._wait_idle()
        before = self.read(0xE0)
        frame_id = self.read(0xE8)
        current_event = self.read(0xE4)
        current_session = self.read(0xEC)
        after = self.read(0xE0)
        if before != after:
            before = after
            frame_id = self.read(0xE8)
            current_event = self.read(0xE4)
            current_session = self.read(0xEC)
            if self.read(0xE0) != before:
                raise RuntimeError("Could not obtain an atomic PL verify snapshot")
        state, reason = before & 0xF, (before >> 4) & 0xF
        count = (before >> 16) & 0xFF
        if current_session != session or current_event != event_sequence:
            frame_id, reason, count = wire.UNKNOWN, 2, 0
        return wire.FVE1_RESULT.pack(frame_id, state, reason, count)


class FoldSession:
    """Protocol ownership, ordering and exact FVE1 retry cache for UDP workers."""

    def __init__(self, allowed_ip, guard, profile, clock=time.monotonic):
        self.allowed_ip = allowed_ip
        self.guard = guard
        self.profile = profile
        self.clock = clock
        self.owner = None
        self.last_sequence = -1
        self.last_tick = -1
        self.last_step = None
        self.last_elapsed = None
        self.last_success = 0
        self.last_event = wire.UNKNOWN
        self.last_result = None
        self.feature_cache = None

    def handle(self, packet, address):
        if address[0] != self.allowed_ip:
            raise ValueError("source_ip")
        frame = wire.decode(packet)
        if frame.magic == wire.MAGIC_FPG1:
            if frame.kind != 1:
                raise ValueError("expected_fpg1_request")
            return self._fpg1(frame, packet, address)
        if frame.magic == wire.MAGIC_FVE1:
            return self._fve1(frame, packet, address)
        raise ValueError("unsupported_magic")

    def _fpg1(self, frame, packet, address):
        values = frame.values
        sample = Sample.unpack(wire.FPG1_REQUEST.pack(*values))
        probe = sample.step == wire.UNKNOWN
        identity = (address, frame.session)
        now = self.clock()
        reset = False
        if self.owner is None:
            if frame.sequence != 0 or not probe:
                raise ValueError("first_request_must_be_probe")
            reset = True
        elif identity != self.owner:
            if now - self.last_success < 5:
                raise ValueError("owner_lease_active")
            if frame.session == self.owner[1] or frame.sequence != 0 or not probe:
                raise ValueError("new_owner_must_use_new_session_probe")
            reset = True
        elif now - self.last_success >= 5:
            raise ValueError("expired_owner_must_start_new_session")
        elif frame.sequence <= self.last_sequence or frame.tick_us <= self.last_tick:
            raise ValueError("old_or_duplicate_fpg1")
        if not self.profile or sample.profile_crc != self.guard.guard_crc:
            raise ValueError("profile_mismatch")
        if probe:
            if frame.sequence != 0:
                raise ValueError("bad_probe_sequence")
            start = tuple(self.profile["start"])
            if (sample.target != start or sample.measured != start or
                    sample.previous != start or sample.act != start or
                    any(sample.correction) or any(sample.previous_correction)):
                raise ValueError("probe_pose_mismatch")
        else:
            if sample.flags & FPG1_RUNNING == 0:
                raise ValueError("task_request_not_running")
            if self.last_step is not None and sample.step <= self.last_step:
                raise ValueError("step_not_increasing")
            if self.last_elapsed is not None and sample.elapsed_us <= self.last_elapsed:
                raise ValueError("elapsed_not_increasing")
        payload = self.guard.check(sample, frame.session, frame.sequence, reset_session=reset)
        self.owner = identity
        self.last_sequence, self.last_tick = frame.sequence, frame.tick_us
        self.last_success = now
        if not probe:
            self.last_step, self.last_elapsed = sample.step, sample.elapsed_us
        result = wire.FPG1_REPLY.unpack(payload)
        self.last_result = result
        self.last_event = result[9]
        if reset:
            self.feature_cache = None
            self.last_step = None
            self.last_elapsed = None
        return wire.encode(wire.MAGIC_FPG1, 3, 2, frame.session, frame.sequence,
                           frame.tick_us, result)

    def _fve1(self, frame, packet, address):
        if frame.kind == 3:
            if self.owner is None:
                return self._fve_reply(frame, (wire.UNKNOWN, 0, 2, 0))
            result = self.guard.query(frame.session, frame.sequence)
            return self._fve_reply(frame, wire.FVE1_RESULT.unpack(result))
        if frame.kind != 1:
            raise ValueError("expected_fve1_feature_or_query")
        if self.feature_cache and self.feature_cache[:2] == (address, packet):
            return self.feature_cache[2]
        result = self.guard.feature(frame)
        response = self._fve_reply(frame, wire.FVE1_RESULT.unpack(result))
        if (self.owner is not None and frame.session == self.owner[1] and
                frame.sequence == self.last_event):
            self.feature_cache = (address, packet, response)
        return response

    @staticmethod
    def _fve_reply(request, values):
        return wire.encode(wire.MAGIC_FVE1, 1, 2 if request.kind == 1 else 4,
                           request.session, request.sequence, request.tick_us, values)
