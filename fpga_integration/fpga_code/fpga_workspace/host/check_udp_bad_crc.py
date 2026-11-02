"""Send one bad-CRC checker frame and verify the board stays silent."""
import argparse
import json
import math
import secrets
import socket
import struct

from .make_udp_probe import make_probe
from .protocol import RESPONSE, frame, unframe
from .reference import evaluate, load_profile


def make_bad_crc_probe(profile, session=1, sequence=0, tick=1):
    sample = make_probe(profile)
    packet = bytearray(frame(1, session, sequence, tick, sample.pack()))
    packet[-1] ^= 1
    return bytes(packet)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--udp', required=True, metavar='HOST:PORT')
    parser.add_argument('--timeout', type=float, default=1.0)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('timeout must be finite and positive')
    try:
        host, port_text = args.udp.rsplit(':', 1)
        address = (host, int(port_text))
        if not host or not 1 <= address[1] <= 65535:
            raise ValueError
    except ValueError:
        parser.error('udp must be HOST:PORT with a valid port')

    profile = load_profile()
    sample = make_probe(profile)
    session = secrets.randbits(32)
    valid_request = frame(1, session, 0, 1, sample.pack())
    bad_packet = make_bad_crc_probe(profile, session, 1, 2)
    expected = evaluate(sample, profile)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(args.timeout)
        sock.connect(address)
        sock.send(valid_request)
        try:
            response = sock.recv(4096)
        except socket.timeout:
            raise SystemExit('FAIL: board did not answer a valid baseline request')
        except OSError as error:
            raise SystemExit('FAIL: valid baseline UDP exchange failed: {}'.format(error))
        try:
            kind, response_session, sequence, tick, payload = unframe(response)
            response_profile, violations, events, phase, _ = RESPONSE.unpack(payload)
        except (ValueError, TypeError, struct.error):
            raise SystemExit('FAIL: baseline reply is malformed')
        if ((kind, response_session, sequence, tick) != (2, session, 0, 1)
                or response_profile != sample.profile_crc
                or (violations, events, phase) != (
                    expected['violations'], expected['events'], expected['phase'])):
            raise SystemExit('FAIL: baseline reply did not match the valid request')

        sock.send(bad_packet)
        try:
            response = sock.recv(4096)
        except socket.timeout:
            print(json.dumps(dict(
                result='PASS', valid_baseline='matched', invalid_request_response='none',
                expected_board_log='DROP CRC mismatch; inspect the board terminal',
                bytes_sent=len(bad_packet),
            ), indent=2))
            return
    raise SystemExit('FAIL: received {} bytes; malformed request was answered'.format(
        len(response)))


if __name__ == '__main__':
    main()
