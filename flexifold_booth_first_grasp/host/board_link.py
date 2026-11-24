"""B35 diagnostic requests with exact reply matching and five-second leases."""
from dataclasses import replace
import secrets
import socket
import time

from . import fold_protocol as wire


class BoardLink:
    def __init__(self, address, profile, crc, timeout=1):
        self.profile, self.crc = profile, crc
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.connect(address)
        self.timeout = timeout
        self.session = self.sequence = self.count = 0
        self.last_success = self.retry_after = 0
        self.last_tick = 0

    def exchange(self, sample):
        tick = max(self.last_tick + 1, time.monotonic_ns() // 1000)
        self.last_tick = tick
        seq = self.sequence
        packet = wire.encode(wire.MAGIC_FPG1, 3, 1, self.session, seq, tick,
                             wire.FPG1_REQUEST.unpack(sample.pack()))
        self.sock.send(packet)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            self.sock.settimeout(max(.001, deadline - time.monotonic()))
            try:
                result = wire.decode(self.sock.recv(4096))
            except socket.timeout:
                break
            except ValueError:
                continue
            if (result.magic, result.kind, result.session, result.sequence, result.tick_us) != (
                    wire.MAGIC_FPG1, 2, self.session, seq, tick):
                continue
            if result.values[0] != self.crc:
                raise ValueError("板端配置 CRC 不匹配，请核对 guard_profile.json。")
            self.sequence += 1
            self.last_success = time.monotonic()
            return result
        raise TimeoutError("未收到匹配的 B35 / FPG1 v3 回包。检查板端服务；等 5 秒后重试。")

    def check(self, sample):
        now = time.monotonic()
        if now < self.retry_after:
            raise TimeoutError("上一会话等待释放，请等 5 秒后重试。")
        try:
            if not self.session or now - self.last_success >= 5:
                self.session = secrets.randbelow(0xFFFFFFFE) + 1
                self.sequence = self.count = 0
                start = tuple(self.profile['start'])
                probe = replace(sample, step=wire.UNKNOWN, flags=0, elapsed_us=0,
                                target=start, measured=start, previous=start, act=start,
                                correction=(0,) * 14, previous_correction=(0,) * 14)
                self.exchange(probe)
            # Each user edit is a new diagnostic tick, preserving the historical pose.
            diagnostic = replace(sample, step=sample.step + self.count,
                                 elapsed_us=sample.elapsed_us + self.count * 40000)
            if diagnostic.step >= self.profile['max_actions']:
                raise ValueError("本轮互动已达样本预算，请等 5 秒后重新开始。")
            result = self.exchange(diagnostic)
            self.count += 1
            return result, diagnostic
        except (OSError, ValueError):
            self.session = 0
            self.retry_after = time.monotonic() + 5.1
            raise

    def close(self):
        self.sock.close()
