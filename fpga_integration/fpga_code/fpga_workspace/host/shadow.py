"""Isolated telemetry-only worker for future shadow comparison; no robot APIs."""
from dataclasses import dataclass
import math
import queue
import secrets
import socket
import struct
import threading
import time

from .protocol import RESPONSE, Sample, frame, unframe


@dataclass(frozen=True)
class ShadowSample:
    step: int
    sample: Sample
    dispatched: bool


@dataclass(frozen=True)
class ShadowResult:
    session: int
    sequence: int
    step: int
    dispatched: bool
    profile_crc: int
    violations: int
    events: int
    phase: int
    board_processing_us: int
    host_round_trip_us: float


class UdpTransport:
    """One connected socket for a single FPGA bridge session."""

    def __init__(self, address, timeout):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be finite and positive')
        self.address = address
        self.timeout = timeout
        self._mismatches = 0
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.connect(address)

    def exchange(self, request):
        started_ns = time.perf_counter_ns()
        deadline = time.monotonic() + self.timeout
        self.socket.send(request)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('No matching shadow UDP reply')
            self.socket.settimeout(remaining)
            response = self.socket.recv(4096)
            if _response_matches(response, request):
                elapsed_us = (time.perf_counter_ns() - started_ns) / 1000
                return response, elapsed_us
            self._mismatches += 1

    def take_mismatches(self):
        mismatches = self._mismatches
        self._mismatches = 0
        return mismatches

    def close(self):
        self.socket.close()


class ShadowClient:
    """Bounded producer queue and one ordered worker; never gates motion."""

    def __init__(self, exchange, on_result=None, capacity=32, session=None):
        if capacity < 1:
            raise ValueError('capacity must be positive')
        self.exchange = exchange
        self.on_result = on_result
        self.session = secrets.randbits(32) if session is None else session
        self._queue = queue.Queue(maxsize=capacity)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._abort = threading.Event()
        self._exchange_closed = False
        self._started = False
        self._accepting = False
        self._sequence = 0
        self._last_tick_us = -1
        self._stats = dict(
            enqueued=0,
            sent=0,
            replied=0,
            timeouts=0,
            errors=0,
            response_mismatches=0,
            queue_full_drops=0,
            shutdown_drops=0,
            stopped_drops=0,
            callback_errors=0,
            high_watermark=0,
        )
        self._thread = threading.Thread(target=self._run, name='fpga-shadow-udp', daemon=True)

    def start(self):
        with self._lock:
            if self._started or self._exchange_closed:
                raise RuntimeError('shadow client is already started or closed')
            self._started = True
            self._accepting = True
            self._thread.start()

    def submit(self, sample):
        try:
            if not isinstance(sample, ShadowSample):
                raise TypeError('sample must be ShadowSample')
            if not isinstance(sample.sample, Sample):
                raise TypeError('sample payload must be Sample')
            sample = ShadowSample(
                sample.step, Sample.unpack(sample.sample.pack()), sample.dispatched)
        except (TypeError, ValueError, OverflowError, struct.error):
            self._increment('errors')
            return False
        with self._lock:
            if not self._accepting:
                self._stats['stopped_drops'] += 1
                return False
            try:
                self._queue.put_nowait(sample)
            except queue.Full:
                self._stats['queue_full_drops'] += 1
                return False
            self._stats['enqueued'] += 1
            self._stats['high_watermark'] = max(
                self._stats['high_watermark'], self._queue.qsize())
            return True

    def close(self, timeout=0.0):
        if timeout is None or not math.isfinite(timeout) or timeout < 0:
            raise ValueError('close timeout must be finite and nonnegative')
        with self._lock:
            self._accepting = False
            self._stop.set()
        if not self._started:
            self._close_exchange()
            return True
        self._thread.join(timeout)
        if self._thread.is_alive():
            with self._lock:
                self._abort.set()
                dropped = 0
                while True:
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        break
                    self._queue.task_done()
                    dropped += 1
                self._stats['shutdown_drops'] += dropped
            return False
        return True

    def stats(self):
        with self._lock:
            result = dict(self._stats)
        result['pending'] = self._queue.qsize()
        result['capacity'] = self._queue.maxsize
        return result

    def _increment(self, key):
        with self._lock:
            self._stats[key] += 1

    def _record_transport_mismatches(self):
        take_mismatches = getattr(self.exchange, 'take_mismatches', None)
        if take_mismatches is not None:
            mismatches = take_mismatches()
            if mismatches:
                with self._lock:
                    self._stats['response_mismatches'] += mismatches

    def _stop_for_sequence_exhaustion(self):
        with self._lock:
            self._accepting = False
            self._stop.set()
            self._abort.set()
            dropped = 1
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
                self._queue.task_done()
                dropped += 1
            self._stats['shutdown_drops'] += dropped

    def _close_exchange(self):
        with self._lock:
            if self._exchange_closed:
                return
            self._exchange_closed = True
        close_transport = getattr(self.exchange, 'close', None)
        if close_transport is not None:
            try:
                close_transport()
            except Exception:
                self._increment('errors')

    def _request(self, item):
        tick_us = max(time.monotonic_ns() // 1000, self._last_tick_us + 1)
        self._last_tick_us = tick_us
        return frame(1, self.session, self._sequence, tick_us, item.sample.pack())

    def _run(self):
        try:
            while True:
                if self._abort.is_set():
                    return
                if self._stop.is_set() and self._queue.empty():
                    return
                try:
                    item = self._queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                try:
                    with self._lock:
                        if self._abort.is_set():
                            self._stats['shutdown_drops'] += 1
                            continue
                    if self._sequence > 0xFFFFFFFF:
                        self._stop_for_sequence_exhaustion()
                        continue
                    try:
                        request = self._request(item)
                    except Exception:
                        self._increment('errors')
                        continue
                    self._increment('sent')
                    self._sequence += 1
                    try:
                        exchange = getattr(self.exchange, 'exchange', self.exchange)
                        response, round_trip_us = exchange(request)
                    except TimeoutError:
                        self._increment('timeouts')
                        continue
                    except Exception:
                        self._increment('errors')
                        continue
                    finally:
                        self._record_transport_mismatches()
                    try:
                        if not _response_matches(response, request):
                            self._increment('response_mismatches')
                            continue
                        _, _, _, _, payload = unframe(response)
                        profile_crc, violations, events, phase, board_us = RESPONSE.unpack(payload)
                    except (TypeError, ValueError, struct.error):
                        self._increment('response_mismatches')
                        continue
                    self._increment('replied')
                    if self.on_result is not None:
                        result = ShadowResult(
                            self.session, self._sequence - 1, item.step, item.dispatched,
                            profile_crc, violations, events, phase, board_us, round_trip_us)
                        try:
                            self.on_result(result)
                        except Exception:
                            self._increment('callback_errors')
                finally:
                    self._queue.task_done()
        finally:
            self._close_exchange()


def _response_matches(response, request):
    try:
        kind, session, sequence, tick, payload = unframe(response)
        _, wanted_session, wanted_sequence, wanted_tick, wanted_payload = unframe(request)
        if (kind, session, sequence, tick) != (2, wanted_session, wanted_sequence, wanted_tick):
            return False
        profile_crc, *_ = RESPONSE.unpack(payload)
    except (TypeError, ValueError, struct.error):
        return False
    return profile_crc == int.from_bytes(wanted_payload[:4], 'little')
