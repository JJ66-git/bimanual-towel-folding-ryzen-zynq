"""Opt-in TX/RX recording around the EXISTING reset entry; no control decisions.

Keep recent pre-command RX and the first 10 seconds after a control TX starts.
Only memory buffering during reset; export after the original entry unwinds.
SDK send return codes are not controller ACKs or physical-holding evidence.
"""
import argparse
from collections import Counter, deque
from contextlib import contextmanager
import json
from pathlib import Path
import runpy
import sys
from threading import Lock
import time

ROOT = Path(__file__).resolve().parents[1]
CONTROL_IDS = {0x150, 0x151, 0x155, 0x156, 0x157, 0x159, 0x470, 0x471, 0x476}
RX_IDS = CONTROL_IDS | {0x2A1, 0x2A5, 0x2A6, 0x2A7, 0x2A8, *range(0x261, 0x267)}


class StartupTrace:
    def __init__(self):
        self.lock = Lock()
        self.pre_rx = deque(maxlen=4096)
        self.events = []
        self.first_control_ns = None
        self.frozen = False
        self.window_closed = False
        self.overflow = 0
        self.recording_error = None
        self.next_tx = 0
        self.anchor = dict(wall_time_ns=time.time_ns(), monotonic_ns=time.perf_counter_ns())

    def record(self, kind, **fields):
        # Recording failures must not add a motion fault or replace SDK results.
        try:
            row = dict(kind=kind, monotonic_ns=time.perf_counter_ns(), **fields)
            with self.lock:
                if self.frozen:
                    return None
                if kind == 'tx_begin':
                    self.next_tx += 1
                    row['tx_id'] = self.next_tx
                if self.first_control_ns is None:
                    if kind == 'tx_begin' and row['can_id'] in CONTROL_IDS:
                        self.first_control_ns = row['monotonic_ns']
                        self.events.extend(self.pre_rx)
                        self.pre_rx.clear()
                    elif kind == 'rx':
                        self.pre_rx.append(row)
                        return None
                if self.first_control_ns is not None and row['monotonic_ns'] - self.first_control_ns > 10_000_000_000:
                    self.window_closed = True
                    return None
                if len(self.events) >= 100_000:
                    self.overflow += 1
                    return None
                self.events.append(row)
                return row.get('tx_id')
        except Exception as error:
            self.recording_error = type(error).__name__
            return None

    @contextmanager
    def observe(self):
        from piper_sdk import C_PiperInterface_V2 as Piper
        from piper_sdk.hardware_port import C_STD_CAN as Transport
        original_send, original_parse, original_enable = Transport.SendCanMessage, Piper.ParseCANFrame, Piper.EnablePiper
        recorder = self

        def send(bus, arbitration_id, data, *args, **kwargs):
            try:
                tx_id = recorder.record('tx_begin', channel=bus.GetCanPortName(),
                    can_id=arbitration_id, data_hex=bytes(data).hex(),
                    dlc=(args[0] if args else kwargs.get('dlc', 8)),
                    is_extended_id=(args[1] if len(args) > 1 else kwargs.get('is_extended_id', False)))
            except Exception as error:
                recorder.recording_error, tx_id = type(error).__name__, None
            try:
                result = original_send(bus, arbitration_id, data, *args, **kwargs)
            except BaseException as error:
                recorder.record('tx_end', tx_id=tx_id, exception=type(error).__name__)
                raise
            try:
                recorder.record('tx_end', tx_id=tx_id, return_status=result.name)
            except Exception as error:
                recorder.recording_error = type(error).__name__
            return result

        def parse(piper, frame):
            entered = time.perf_counter_ns()
            fields = None
            try:
                if frame.is_error_frame or frame.arbitration_id in RX_IDS:
                    fields = dict(channel=piper.GetCanBus().GetCanPortName(), can_id=frame.arbitration_id,
                        data_hex=bytes(frame.data).hex(), can_timestamp_s=frame.timestamp,
                        is_error_frame=frame.is_error_frame)
            except Exception as error:
                recorder.recording_error = type(error).__name__
            try:
                result = original_parse(piper, frame)
            except BaseException as error:
                recorder.record('rx_parse_error', entered_monotonic_ns=entered,
                    exception=type(error).__name__, **(fields or {}))
                raise
            if fields is not None:
                recorder.record('rx', entered_monotonic_ns=entered, **fields)
            return result

        def enable(piper, *args, **kwargs):
            channel = None
            try:
                channel = piper.GetCanBus().GetCanPortName()
                recorder.record('api_begin', api='EnablePiper', channel=channel)
            except Exception as error:
                recorder.recording_error = type(error).__name__
            try:
                result = original_enable(piper, *args, **kwargs)
            except BaseException as error:
                recorder.record('api_end', api='EnablePiper', channel=channel, exception=type(error).__name__)
                raise
            recorder.record('api_end', api='EnablePiper', channel=channel, returned=result)
            return result

        Transport.SendCanMessage, Piper.ParseCANFrame, Piper.EnablePiper = send, parse, enable
        try:
            yield
        finally:
            with self.lock:
                self.frozen = True
            Transport.SendCanMessage, Piper.ParseCANFrame, Piper.EnablePiper = original_send, original_parse, original_enable

    def save(self, directory, entry, arguments, original_error, stopped):
        with self.lock:
            rows = self.events + (list(self.pre_rx) if self.first_control_ns is None else [])
        rows = sorted(rows, key=lambda row: row.get('entered_monotonic_ns', row['monotonic_ns']))
        with (directory / 'timeline.jsonl').open('w', encoding='utf-8') as output:
            for row in rows:
                output.write(json.dumps(row, allow_nan=False) + '\n')
        summary = dict(schema='reset-start-txrx-v1', status='ORIGINAL_ERROR' if original_error else 'COMPLETE',
            original_error=original_error, entry=str(entry), original_arguments=arguments,
            execute='--execute' in arguments, stopped_by_original_handler=stopped,
            event_counts=dict(Counter(row['kind'] for row in rows)), time_anchor=self.anchor,
            first_control_monotonic_ns=self.first_control_ns, recording_window_s=10,
            recording_window_closed=self.window_closed, overflow_events=self.overflow,
            recording_error=self.recording_error, physical_holding_verified=False,
            scope='SDK send calls/results + RX callback entry/parse-return, not wire/controller ACK',
            pre_command_rx_scope='latest 4096 selected RX frames; older pre-command RX is not retained',
            limits='Recording window/capacity affect only evidence; no control state/limit/threshold changes')
        (directory / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False), encoding='utf-8')
        print('RESET_START_TRACE=' + json.dumps(dict(summary=str(directory / 'summary.json'),
            timeline=str(directory / 'timeline.jsonl'), status=summary['status'],
            recording_error=self.recording_error, overflow_events=self.overflow)), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('reset_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.reset_args[1:] if args.reset_args[:1] == ['--'] else args.reset_args
    entry = ROOT / 'deployment_entry/cobot_reset.py'
    # Ordinary output creation happens before entering the original reset/MOVE.
    args.out_dir.mkdir(parents=True, exist_ok=False)
    (args.out_dir / 'summary.json').write_text(json.dumps(dict(status='RECORDING')), encoding='utf-8')
    recorder, original_error, namespace = StartupTrace(), None, {}
    old_argv, old_path = sys.argv, sys.path.copy()
    try:
        sys.argv = [str(entry), *arguments]
        sys.path[:] = [str(entry.parent), *old_path[1:]]
        with recorder.observe():
            namespace = runpy.run_path(str(entry), run_name='__main__')
    except BaseException as error:
        original_error = f'{type(error).__name__}: {error}'
        raise
    finally:
        sys.argv = old_argv
        sys.path[:] = old_path
        try:
            recorder.save(args.out_dir, entry, arguments, original_error, namespace.get('stop_requested', False))
        except Exception as error:
            print('RESET_START_TRACE_WRITE_ERROR=' + str(error) + '; folder=' + str(args.out_dir),
                  file=sys.stderr, flush=True)


if __name__ == '__main__':
    main()
