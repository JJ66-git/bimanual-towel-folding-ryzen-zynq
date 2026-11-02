"""Monitor-only lifecycle and structured diagnostics over the existing B32 registers."""
import json
import time

from host import watchdog_protocol as wire
from host.protocol import Sample, UNKNOWN
from host.reference import profile_crc

BUILD_ID = 0x46504732


class Journal:
    def __init__(self, path):
        self.output = path.open('x', encoding='utf-8')

    def write(self, event, **values):
        self.output.write(json.dumps(dict(event=event, board_monotonic_ns=time.monotonic_ns(),
                                         **values), separators=(',', ':')) + '\n')
        self.output.flush()

    def close(self):
        self.output.close()

    def watchdog_sample(self, snapshot):
        pass

    @property
    def running(self):
        return True


def read_u64(device, low, high):
    for _ in range(4):
        upper = device.read(high)
        lower = device.read(low)
        if upper == device.read(high):
            return (upper << 32) | lower
    raise RuntimeError('Unstable watchdog counter read')


class Monitor:
    def __init__(self, device, profile, journal):
        self.device = device
        self.profile = profile_crc(profile)
        self.journal = journal
        self.identity = None
        self.sequence = self.tick = -1
        self.state = wire.IDLE
        self.last_count = self.device.read(0x060)
        self.device.write(0x04c, 0)

    def snapshot(self):
        return dict(build_id=BUILD_ID, status=self.device.read(0x054),
                    timeout_count=self.device.read(0x060), state=self.state,
                    age_cycles=read_u64(self.device, 0x058, 0x05c),
                    last_gap_cycles=read_u64(self.device, 0x064, 0x068))

    def observe_timeout(self, snapshot):
        count = snapshot['timeout_count']
        if count > self.last_count:
            self.journal.write('watchdog_expired', watchdog=snapshot, previous_count=self.last_count,
                               session=self.identity[1] if self.identity else None,
                               reason='periodic_action_observation_gap; root cause unknown')
        self.last_count = count

    def poll(self):
        snapshot = self.snapshot()
        self.observe_timeout(snapshot)
        self.journal.watchdog_sample(snapshot)
        return snapshot

    def handle(self, data, address, received_ns):
        op, session, sequence, tick, payload = wire.unpack(data)
        identity = (address, session)
        if self.identity is None:
            if op != wire.BEGIN or sequence != 0:
                raise ValueError('First diagnostic packet must be BEGIN sequence zero')
        elif identity != self.identity:
            raise ValueError('Different diagnostic source/session; restart service')
        if sequence <= self.sequence or tick <= self.tick:
            raise ValueError('Old diagnostic sequence/tick')
        if self.state == wire.ENDED or (self.identity is not None and op == wire.BEGIN):
            raise ValueError('Diagnostic session already started or ended')
        if op == wire.PAUSE and self.state != wire.ACTIVE:
            raise ValueError('PAUSE requires PERIODIC mode')
        if op == wire.RESUME and self.state != wire.PAUSED:
            raise ValueError('RESUME requires paused PERIODIC mode')
        if op in (wire.ALLOW, wire.HOLD) and self.state not in (wire.ACTIVE, wire.RESULT_ONLY):
            raise ValueError('Action observation requires an active mode')
        sample = Sample.unpack(payload) if op in (wire.ALLOW, wire.HOLD) else None
        if sample is not None and sample.profile_crc != self.profile:
            raise ValueError('Diagnostic profile mismatch')

        details = dict(peer=list(address), session=session, sequence=sequence,
                       sender_tick_us=tick, board_received_ns=received_ns, operation=wire.NAMES[op])
        checks = dict(violations=UNKNOWN, events=UNKNOWN, phase=UNKNOWN)
        event = 'status'
        if op == wire.BEGIN:
            mode = wire.MODE.unpack(payload)[0]
            self.device.write(0x04c, int(mode == wire.PERIODIC))
            self.state = wire.ACTIVE if mode == wire.PERIODIC else wire.RESULT_ONLY
            event = 'session_started'
        elif sample is not None:
            details['pl_check_started_ns'] = time.monotonic_ns()
            result = self.device.check(sample)
            details['pl_check_done_ns'] = time.monotonic_ns()
            checks = dict(zip(('violations', 'events', 'phase'), result))
            self.observe_timeout(self.snapshot())
            details.update(sample_step=sample.step, target=list(sample.target),
                           commit_written=self.state == wire.ACTIVE)
            if self.state == wire.ACTIVE:
                details['commit_before_ns'] = time.monotonic_ns()
                self.device.write(0x050, 1)
                details['commit_after_ns'] = time.monotonic_ns()
            event = 'action_observed'
        elif op == wire.PAUSE:
            self.device.write(0x04c, 0)
            self.state = wire.PAUSED
            event = 'monitor_paused'
        elif op == wire.RESUME:
            self.device.write(0x04c, 1)
            self.state = wire.ACTIVE
            event = 'monitor_resumed'
        elif op == wire.END:
            self.device.write(0x04c, 0)
            self.state = wire.ENDED
            event = 'session_ended'
        self.identity = identity
        details['sequence_gap'] = sequence - self.sequence - 1
        self.sequence, self.tick = sequence, tick
        snapshot = self.poll()
        self.journal.write(event, **details, watchdog=snapshot, **checks)
        return wire.response(op, session, sequence, tick, dict(snapshot, **checks))

    def close(self):
        self.device.write(0x04c, 0)
        snapshot = self.snapshot()
        self.observe_timeout(snapshot)
        self.journal.write('server_stopped', watchdog=snapshot)
