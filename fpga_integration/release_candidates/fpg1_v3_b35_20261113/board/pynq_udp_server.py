"""PYNQ Overlay UDP-to-PL checker; never opens robot or CAN interfaces."""
import argparse
import math
from pathlib import Path
import signal
import socket
import time

from board.watchdog_monitor import Journal, Monitor
from board.task_monitor import Inbox
from board.ownership import exclusive_owner
from host import task_status
from host import watchdog_protocol
from host.protocol import RESPONSE, Sample, frame, unframe
from host.reference import load_profile, profile_crc


PACKAGE_BITFILE = Path(__file__).resolve().parents[1] / "overlay" / "own_hybrid_motion_guard_watchdog.bit"
LOCAL_BITFILE = Path(__file__).resolve().parents[1] / "own_hybrid_motion_guard_watchdog.bit"
DEFAULT_BITFILE = PACKAGE_BITFILE if PACKAGE_BITFILE.exists() else LOCAL_BITFILE
BUILD_ID = 0x46504732
VECTOR_BASES = (0x100, 0x140, 0x180, 0x1C0, 0x200, 0x240)


class PynqGuard:
    def __init__(self, ip, timeout=0.1):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self.ip = ip
        self.timeout = timeout
        build_id = self.read(0x00)
        if build_id != BUILD_ID:
            raise RuntimeError("Unexpected motion guard BUILD_ID: 0x{:08x}".format(build_id))

    def read(self, offset):
        return int(self.ip.read(offset))

    def write(self, offset, value):
        self.ip.write(offset, value & 0xFFFFFFFF)

    def check(self, sample):
        if self.read(0x08) & 0x1:
            raise RuntimeError("PL busy before request")
        for offset, value in ((0x10, sample.profile_crc), (0x14, sample.step),
                              (0x18, sample.flags), (0x1C, sample.feedback_age_us),
                              (0x20, sample.queue_depth)):
            self.write(offset, value)
        vectors = (sample.target, sample.measured, sample.previous, sample.act,
                   sample.correction, sample.previous_correction)
        for base, vector in zip(VECTOR_BASES, vectors):
            for index, value in enumerate(vector):
                self.write(base + index * 4, value)

        self.write(0x04, 1)
        deadline = time.monotonic() + self.timeout
        while True:
            status = self.read(0x08)
            if status & 0x2 and not status & 0x1:
                return self.read(0x40), self.read(0x44), self.read(0x48)
            if time.monotonic() >= deadline:
                raise TimeoutError("PL result timeout; no motion decision produced")
            time.sleep(0.0001)


def serve_packet(packet, device, profile):
    kind, session, sequence, tick, payload = unframe(packet)
    if kind != 1:
        raise ValueError("Expected request")
    sample = Sample.unpack(payload)
    if sample.profile_crc != profile_crc(profile):
        raise ValueError("Host/PS profile mismatch")
    started = time.monotonic_ns()
    violations, events, phase = device.check(sample)
    elapsed = min(0xFFFFFFFF, (time.monotonic_ns() - started) // 1000)
    response = RESPONSE.pack(sample.profile_crc, violations, events, phase, elapsed)
    return frame(2, session, sequence, tick, response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bitfile", type=Path, default=DEFAULT_BITFILE)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9009)
    parser.add_argument("--peer", required=True, help="allowed host IPv4 address")
    parser.add_argument("--pl-timeout", type=float, default=0.1)
    parser.add_argument("--log", type=Path, required=True, help="new JSONL diagnostics file; existing files are not overwritten")
    args = parser.parse_args()
    if not math.isfinite(args.pl_timeout) or args.pl_timeout <= 0 or not 1 <= args.port <= 65535:
        parser.error("pl-timeout and port must be positive and valid")
    if not args.bitfile.is_file():
        parser.error("bitstream not found: {}".format(args.bitfile))
    if args.log.exists() or not args.log.parent.is_dir():
        parser.error("log must be a new file in an existing directory")

    with exclusive_owner():
        from pynq import Overlay

        overlay = Overlay(str(args.bitfile))
        print("IPs:", list(overlay.ip_dict), flush=True)
        device = PynqGuard(overlay.guard, args.pl_timeout)
        print("BUILD_ID=0x{:08x}; binding UDP {}:{}".format(
            BUILD_ID, args.bind, args.port), flush=True)
        if args.peer:
            print("Allowed peer:", args.peer, flush=True)

        profile = load_profile()
        journal = Journal(args.log)
        try:
            serve(device, profile, journal, args.bind, args.port, args.peer)
        finally:
            journal.close()


def serve(device, profile, journal, bind, port, peer):
    monitor = Monitor(device, profile, journal)
    tasks = Inbox(journal)
    active = None
    last_sequence = -1
    last_tick = -1
    def stop(signum, current_frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind((bind, port))
            sock.settimeout(0.05)
            journal.write("server_started", build_id=BUILD_ID, bind=bind, port=port,
                          peer=peer, board_wall_clock_trusted=False,
                          action_source="sender_claimed_observation; not robot acknowledgement")
            next_poll = 0
            while journal.running:
                if time.monotonic() >= next_poll:
                    monitor.poll()
                    next_poll = time.monotonic() + 0.05
                try:
                    packet, address = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                received_ns = time.monotonic_ns()
                try:
                    if peer and address[0] != peer:
                        raise ValueError("Source IP is not the configured peer")
                    if packet.startswith(task_status.MAGIC):
                        reply = tasks.handle(packet, address, received_ns)
                        sock.sendto(reply, address)
                        continue
                    if packet.startswith(watchdog_protocol.MAGIC):
                        reply = monitor.handle(packet, address, received_ns)
                        sent = sock.sendto(reply, address)
                        journal.write("diagnostic_reply_sent", peer=list(address), bytes=sent,
                                      sequence=monitor.sequence)
                        continue
                    kind, session, sequence, tick, _ = unframe(packet)
                    print("RX from {}:{} session={} seq={} tick={} bytes={}".format(
                        address[0], address[1], session, sequence, tick, len(packet)), flush=True)
                    identity = (address, session)
                    if active is not None and identity != active:
                        raise ValueError("Different session; restart bridge explicitly to re-arm")
                    if kind != 1 or sequence <= last_sequence or tick <= last_tick:
                        raise ValueError("Old, duplicate or invalid request")
                    if active is None and sequence != 0:
                        raise ValueError("First sequence must be zero")
                    reply = serve_packet(packet, device, profile)
                    _, _, _, _, response_payload = unframe(reply)
                    response_profile, violations, events, phase, elapsed = RESPONSE.unpack(response_payload)
                    active = identity
                    last_sequence = sequence
                    last_tick = tick
                    sent = sock.sendto(reply, address)
                    journal.write("check_reply_sent", peer=list(address), session=session,
                                  sequence=sequence, sender_tick_us=tick, board_received_ns=received_ns,
                                  profile=response_profile, violations=violations, events=events,
                                  phase=phase, board_processing_us=elapsed, bytes=sent,
                                  commit_written=False)
                    print("TX to {}:{} seq={} profile={:08x} violations={:08x} events={:08x} phase={} elapsed_us={} bytes={}".format(
                        address[0], address[1], sequence, response_profile,
                        violations, events, phase, elapsed, sent), flush=True)
                except ValueError as error:
                    journal.write("packet_rejected", peer=list(address), bytes=len(packet),
                                  board_received_ns=received_ns, untrusted_header_hex=packet[:24].hex(),
                                  error=str(error))
                    print("DROP", error, flush=True)
    except KeyboardInterrupt:
        print("Stopped", flush=True)
    finally:
        monitor.close()


if __name__ == "__main__":
    main()
