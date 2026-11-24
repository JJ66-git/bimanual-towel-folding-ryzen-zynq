"""Host-side FPG1 v3/FVE1 v1 UDP client for a background observer worker."""
import math
import secrets
import socket
import time

from . import fold_protocol as wire


def _feature_value(feature, baseline_area):
    if feature is None or not isinstance(feature, dict):
        return 0, 0, 0, 0, 0, 0
    try:
        area = float(feature["area"])
        center = feature["center"]
        rectangularity = float(feature["rectangularity"])
        dark_fraction = float(feature["dark_fraction"])
        baseline = float(baseline_area)
        values = (area, baseline, float(center[0]), float(center[1]),
                  rectangularity, dark_fraction)
        if not all(math.isfinite(value) for value in values) or baseline <= 0 or area < 0:
            return 0, 0, 0, 0, 0, 0
        area_bp = math.floor(area / baseline * 10000 + 0.5)
        cx = math.floor(float(center[0]) + 0.5)
        cy = math.floor(float(center[1]) + 0.5)
        rect_bp = math.floor(rectangularity * 10000 + 0.5)
        dark_bp = math.floor(dark_fraction * 10000 + 0.5)
        if not 0 <= area_bp <= 0xFFFF or not 0 <= cx <= 0xFFFF or not 0 <= cy <= 0xFFFF:
            return 0, 0, 0, 0, 0, 0
        if not 0 <= rect_bp <= 10000 or not 0 <= dark_bp <= 10000:
            return 0, 0, 0, 0, 0, 0
        return area_bp, cx, cy, rect_bp, dark_bp, 1
    except (KeyError, TypeError, ValueError, OverflowError):
        return 0, 0, 0, 0, 0, 0


def feature_values(feature, baseline_area):
    """Return `(area,cx,cy,rectangularity,dark,flags)` in wire units."""
    return _feature_value(feature, baseline_area)


class FoldClient:
    """Two-socket client; callers should keep all methods on one worker thread."""

    def __init__(self, address, profile_crc, *, timeout=0.2, session=None,
                 socket_factory=socket.socket, monotonic_ns=time.monotonic_ns):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        host, port = address
        if not 1 <= int(port) <= 65535:
            raise ValueError("invalid UDP port")
        self.address = (host, int(port))
        self.profile_crc = int(profile_crc) & wire.UNKNOWN
        self.timeout = timeout
        self.session = session or secrets.randbelow(0xFFFFFFFE) + 1
        self.monotonic_ns = monotonic_ns
        self.control = socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        self.vision = socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        self.control.settimeout(timeout)
        self.vision.settimeout(timeout)
        self.control.connect(self.address)
        self.vision.connect(self.address)
        self.sequence = 0
        self.last_tick = -1
        self.last_event = wire.UNKNOWN

    def _tick(self):
        tick = self.monotonic_ns() // 1000
        self.last_tick = max(tick, self.last_tick + 1)
        return self.last_tick

    def _exchange(self, sock, request, expected_magic, expected_kind, *, attempts=1):
        wanted=wire.decode(request)
        for attempt in range(attempts):
            deadline = time.monotonic() + self.timeout
            sock.settimeout(self.timeout)
            sock.send(request)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                sock.settimeout(remaining)
                try:
                    response = sock.recv(4096)
                except socket.timeout:
                    break
                try:
                    decoded = wire.decode(response)
                except ValueError:
                    continue
                if ((decoded.magic, decoded.kind, decoded.session, decoded.sequence,
                     decoded.tick_us) ==
                    (expected_magic, expected_kind, wanted.session, wanted.sequence,
                     wanted.tick_us)):
                    return decoded
        return None

    def probe(self, sample):
        if sample.step != wire.UNKNOWN or sample.flags != 0 or sample.elapsed_us != 0:
            raise ValueError("probe sample must use UNKNOWN step, zero flags and elapsed")
        tick = self._tick()
        request = wire.encode(wire.MAGIC_FPG1, 3, 1, self.session, 0, tick,
                              wire.FPG1_REQUEST.unpack(sample.pack()))
        response = self._exchange(self.control, request, wire.MAGIC_FPG1, 2)
        if response is None:
            raise TimeoutError("no matching FPG1 v3 probe reply")
        if response.values[8] != wire.BUILD_ID or response.values[0] != self.profile_crc:
            raise RuntimeError("probe reply BUILD_ID/profile does not match client")
        self.sequence = 1
        return response

    def send_sample(self, sample):
        if self.sequence > 0xFFFFFFFE:
            raise RuntimeError("FPG1 sequence exhausted; start a new session")
        if self.sequence == 0:
            raise RuntimeError("FPG1 probe must complete before task samples")
        tick = self._tick()
        sequence = self.sequence
        self.sequence += 1
        request = wire.encode(wire.MAGIC_FPG1, 3, 1, self.session, sequence, tick,
                              wire.FPG1_REQUEST.unpack(sample.pack()))
        response = self._exchange(self.control, request, wire.MAGIC_FPG1, 2)
        if response is None:
            return None
        if response.values[8] != wire.BUILD_ID or response.values[0] != self.profile_crc:
            raise RuntimeError("FPG1 v3 reply BUILD_ID/profile does not match client")
        self.last_event = response.values[9]
        return response

    def send_feature(self, event_sequence, event_rx_us, frame_id, frame_ready_us,
                     feature, baseline_area):
        if event_sequence == wire.UNKNOWN or frame_id == wire.UNKNOWN:
            raise ValueError("event sequence and consumed frame id must be known")
        tick = int(frame_ready_us)
        values = (int(event_rx_us), int(frame_id), *feature_values(feature, baseline_area))
        request = wire.encode(wire.MAGIC_FVE1, 1, 1, self.session, int(event_sequence),
                              tick, values)
        response = self._exchange(self.vision, request, wire.MAGIC_FVE1, 2, attempts=2)
        if response is not None:
            return response
        for _ in range(3):
            query = wire.encode(wire.MAGIC_FVE1, 1, 3, self.session,
                                int(event_sequence), self._tick(), ())
            response = self._exchange(self.vision, query, wire.MAGIC_FVE1, 4)
            if response is not None and response.values[1] in (2, 3):
                return response
        return response

    def query_event(self, event_sequence, attempts=3):
        last = None
        for _ in range(min(3, max(1, attempts))):
            request = wire.encode(wire.MAGIC_FVE1, 1, 3, self.session,
                                  int(event_sequence), self._tick(), ())
            last = self._exchange(self.vision, request, wire.MAGIC_FVE1, 4)
            if last is not None and last.values[1] in (2, 3):
                break
        return last

    def close(self):
        self.control.close()
        self.vision.close()
