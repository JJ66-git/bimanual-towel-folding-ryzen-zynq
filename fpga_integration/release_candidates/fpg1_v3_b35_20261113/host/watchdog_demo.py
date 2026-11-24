"""Synthetic FPGA diagnostic exercise; never opens robot interfaces."""
import argparse
from dataclasses import replace
import json
import math
from pathlib import Path
import secrets
import socket
import time

from host import watchdog_protocol as wire
from host.link import response_matches
from host.protocol import RESPONSE, RUNNING, Sample, frame
from host.reference import emulate, load_profile, profile_crc


class Client:
    def __init__(self, address, timeout, output):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.connect(address)
        self.timeout = timeout
        self.output = output
        self.session = secrets.randbits(32)
        self.sequence = 0
        self.tick = -1
        self.last_request = None
        self.records = []

    def record(self, **values):
        self.output.write(json.dumps(dict(input_source='synthetic_test', **values)) + '\n')
        self.output.flush()

    def make_request(self, op, payload=b'', session=None):
        self.tick = max(time.perf_counter_ns() // 1000, self.tick + 1)
        return wire.request(op, self.session if session is None else session,
                            self.sequence, self.tick, payload)

    def exchange(self, label, op, payload=b''):
        request = self.make_request(op, payload)
        identity = wire.unpack(request)[:4]
        started = time.perf_counter_ns()
        self.sock.send(request)
        deadline = time.perf_counter() + self.timeout
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise TimeoutError('No matching diagnostic reply')
            self.sock.settimeout(remaining)
            data = self.sock.recv(4096)
            try:
                received = wire.read_response(data)
                if received[:4] != identity:
                    continue
                values = received[4]
                if values['build_id'] != 0x46504732:
                    raise RuntimeError('Unexpected hardware build')
                break
            except ValueError:
                continue
        finished = time.perf_counter_ns()
        row = dict(label=label, operation=wire.NAMES[op], session=self.session,
                   sequence=self.sequence, sender_tick_us=self.tick,
                   host_sent_ns=started, host_received_ns=finished,
                   host_round_trip_us=(finished-started)/1000, response=values)
        self.records.append(row)
        self.record(event='diagnostic_exchange', **row)
        self.sequence += 1
        self.last_request = request
        return values

    def rejected_probe(self, label, data):
        started = time.perf_counter_ns()
        self.sock.send(data)
        self.sock.settimeout(0.08)
        try:
            self.sock.recv(4096)
        except socket.timeout:
            self.record(event='probe_rejected', label=label, host_sent_ns=started,
                        host_wait_finished_ns=time.perf_counter_ns())
            return
        raise RuntimeError('Invalid probe unexpectedly received a reply: ' + label)

    def check_fpg1(self, sample, profile):
        request = frame(1, secrets.randbits(32), 0, time.perf_counter_ns() // 1000, sample.pack())
        self.sock.send(request)
        self.sock.settimeout(self.timeout)
        reply = self.sock.recv(4096)
        if not response_matches(reply, request):
            raise RuntimeError('FPG1 probe reply does not match')
        from host.protocol import unframe
        result = RESPONSE.unpack(unframe(reply)[4])
        expected = RESPONSE.unpack(unframe(emulate(request, profile))[4])
        if result[:4] != expected[:4]:
            raise RuntimeError('FPG1 probe differs from integer reference')
        self.record(event='fpg1_probe', expected_match=True, commit_expected=False)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(client, mode):
    profile = load_profile()
    pose = tuple(profile['start'])
    sample = Sample(profile_crc(profile), 0, RUNNING, 0, 0xffffffff, 0xffffffff,
                    pose, pose, pose, pose, (0,)*14, (0,)*14)
    periodic = mode == 'periodic'
    client.rejected_probe('status_before_begin', client.make_request(wire.STATUS))
    result = client.exchange('begin', wire.BEGIN, wire.MODE.pack(wire.PERIODIC if periodic else wire.ON_RESULT))
    require(result['status'] == (1 if periodic else 0), 'Unexpected initial mode')
    client.rejected_probe('repeated_begin', client.make_request(wire.BEGIN, wire.MODE.pack(wire.PERIODIC)))
    client.rejected_probe('resume_without_pause', client.make_request(wire.RESUME))
    if not periodic:
        client.rejected_probe('pause_on_result', client.make_request(wire.PAUSE))
    time.sleep(0.65)
    result = client.exchange('before_first_action', wire.STATUS)
    require(result['status'] == (1 if periodic else 0) and result['timeout_count'] == 0,
            'Watchdog armed before the first action')
    result = client.exchange('first_allow', wire.ALLOW, sample.pack())
    require(result['status'] == (3 if periodic else 0), 'ALLOW did not produce the expected state')
    time.sleep(0.04)
    result = client.exchange('hold', wire.HOLD, replace(sample, step=1).pack())
    require(result['status'] == (3 if periodic else 0), 'HOLD did not produce the expected state')
    if periodic:
        duplicate = client.last_request
        client.rejected_probe('duplicate', duplicate)
        client.rejected_probe('old_tick', wire.request(wire.STATUS, client.session, client.sequence,
                                                       wire.unpack(duplicate)[3]))
        corrupt = bytearray(client.make_request(wire.ALLOW, replace(sample, step=2).pack()))
        corrupt[-1] ^= 1
        client.rejected_probe('bad_crc', bytes(corrupt))
        client.rejected_probe('wrong_profile', client.make_request(wire.ALLOW, replace(sample, profile_crc=0).pack()))
        client.rejected_probe('foreign_session', client.make_request(wire.STATUS, session=client.session ^ 1))
        client.check_fpg1(sample, profile)
        time.sleep(0.30)
        result = client.exchange('quiet_timeout', wire.STATUS)
        require(result['status'] == 7 and result['timeout_count'] == 1, 'Silence did not expire once')
        require(result['age_cycles'] >= 25_000_000, 'Early PL timeout')
        time.sleep(0.10)
        result = client.exchange('timeout_still_once', wire.STATUS)
        require(result['timeout_count'] == 1, 'One silence was counted repeatedly')
        result = client.exchange('recover', wire.ALLOW, replace(sample, step=2).pack())
        require(result['status'] == 3 and result['last_gap_cycles'] >= 25_000_000,
                'New action failed to recover or preserve gap')
        result = client.exchange('pause', wire.PAUSE)
        require(result['status'] & 3 == 0, 'PAUSE did not disarm')
        client.rejected_probe('action_while_paused', client.make_request(wire.ALLOW, sample.pack()))
        time.sleep(0.65)
        result = client.exchange('paused_quiet', wire.STATUS)
        require(result['status'] & 3 == 0 and result['timeout_count'] == 1, 'Paused stream expired')
        result = client.exchange('resume', wire.RESUME)
        require(result['status'] == 1, 'RESUME armed without a new action')
        time.sleep(0.65)
        result = client.exchange('resumed_before_action', wire.STATUS)
        require(result['status'] == 1 and result['timeout_count'] == 1, 'RESUME did not wait for a new action')
        result = client.exchange('resumed_hold', wire.HOLD, replace(sample, step=3).pack())
        require(result['status'] == 3, 'Resumed HOLD failed to arm')
    else:
        time.sleep(0.65)
        result = client.exchange('on_result_quiet', wire.STATUS)
        require(result['status'] == 0 and result['timeout_count'] == 0, 'ON_RESULT applied a periodic deadline')
    result = client.exchange('end', wire.END)
    require(result['status'] & 3 == 0, 'END left monitoring armed')
    client.rejected_probe('status_after_end', client.make_request(wire.STATUS))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--udp', required=True, metavar='HOST:PORT')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--mode', choices=('periodic', 'on-result'), default='periodic')
    parser.add_argument('--timeout', type=float, default=1)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('timeout must be finite and positive')
    if args.out.exists():
        parser.error('output directory must be new')
    host, port = args.udp.rsplit(':', 1)
    args.out.mkdir(parents=True)
    summary = dict(passed=False, input_source='synthetic_test', mode=args.mode,
                   robot_commands=False, watchdog_timeout_ms_provisional=500)
    with (args.out/'events.jsonl').open('x', encoding='utf-8') as output:
        client = Client((host, int(port)), args.timeout, output)
        try:
            run(client, args.mode)
            summary['passed'] = True
        except Exception as error:
            summary['error'] = str(error)
            raise
        finally:
            client.sock.close()
            summary['exchanges'] = len(client.records)
            summary['last_response'] = client.records[-1]['response'] if client.records else None
            (args.out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    print('PASS: synthetic diagnostic ' + args.mode + '; no robot commands')


if __name__ == '__main__':
    main()
