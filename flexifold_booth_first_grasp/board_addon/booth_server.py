"""Run inside the deployed B34 runtime; show guard violations on its LCD."""
import argparse
import ipaddress
from pathlib import Path
import socket
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True, type=Path, help='existing B34 runtime root')
    parser.add_argument('--peer', required=True, type=ipaddress.IPv4Address)
    parser.add_argument('--port', type=int, default=9009)
    parser.add_argument('--log-dir', required=True, type=Path, help='new output directory')
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    if not (runtime / 'board/fold_bridge.py').is_file(): parser.error('not a B34 runtime')
    if not 1 <= args.port <= 65535: parser.error('invalid port')
    if args.log_dir.exists(): parser.error('log-dir must be new')
    # Resolve the deployed runtime's board/host packages rather than booth host modules.
    sys.path.insert(0, str(runtime))
    from board.fold_bridge import FoldSession, FoldPynqGuard
    from board.ownership import exclusive_owner
    from board.clock_reference import configure_reference, verify_fclk0
    from board.fpg1_v3_server import DEFAULT_BITFILE
    from board.watchdog_monitor import Journal
    from host.reference import load_profile
    from host import fold_protocol as wire
    from booth_display import BoothDisplay
    from pynq import Overlay
    args.log_dir.mkdir(parents=True)
    with exclusive_owner():
        configure_reference()
        overlay = Overlay(str(DEFAULT_BITFILE)); verify_fclk0()
        profile = load_profile()
        session = FoldSession(str(args.peer), FoldPynqGuard(overlay.guard, profile), profile)
        journal = Journal(args.log_dir / 'booth.jsonl')
        display = BoothDisplay(overlay.lcd, args.log_dir)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.bind(('0.0.0.0', args.port)); sock.settimeout(.2)
                print('Booth B34 / recorded_replay / peer {}'.format(args.peer), flush=True)
                while journal.running:
                    if display.error: raise RuntimeError(display.error)
                    try: packet, address = sock.recvfrom(4096)
                    except socket.timeout:
                        if display.error: raise RuntimeError(display.error)
                        continue
                    try:
                        request = wire.decode(packet)
                        if request.magic != wire.MAGIC_FPG1: raise ValueError('booth_fpg1_only')
                        reply = session.handle(packet, address)
                        result = wire.decode(reply)
                        sock.sendto(reply, address)
                        if request.values[1] != wire.UNKNOWN:
                            # Only a successful PL transaction may replace the displayed result.
                            display.update(mask=result.values[1], sequence=result.sequence,
                                session=result.session, value=request.values[6 + 8] / 1e6)
                        journal.write('booth_reply', source='recorded_replay_with_j2_edit',
                                      request_hex=packet.hex(), response_hex=reply.hex())
                    except ValueError as error:
                        journal.write('booth_rejected', error=str(error))
        except KeyboardInterrupt:
            pass
        finally:
            display.close(); journal.close()


if __name__ == '__main__': main()
