"""Optional, bounded, process-local display telemetry. Never calls control APIs.

Only the policy loop owns counters. The GUI samples immutable snapshots; dropping
GUI packets cannot drop a dispatched control step from the counts. Publishing is
best effort and uses pointer assignment, with no queues, callbacks or waits.
"""
from dataclasses import dataclass, replace
import math
import time
import uuid


@dataclass(frozen=True)
class ControlSnapshot:
    run_id: str
    sampled_mono_s: float
    started_mono_s: float
    step: int = -1
    authority: float | None = None
    max_authority: float | None = None
    max_correction_rad: float | None = None
    source: str = ''
    corrected_steps: int = 0
    dispatched_steps: int = 0
    candidate_steps: int = 0
    max_actions: int = 0
    run_state: str = 'preparing'
    execute: bool = False
    elapsed_s: float = 0.
    detail: str = ''


@dataclass(frozen=True)
class BoardSnapshot:
    sampled_mono_s: float
    session: int
    sequence: int
    step: int
    reference_match: bool
    violations: int
    events: int = 0
    phase: int = 0
    round_trip_ms: float = 0.
    board_processing_ms: float = 0.
    checked_count: int = 0
    reference_matches: int = 0
    reference_mismatches: int = 0
    violation_count: int = 0
    protocol_version: int = 1
    build_id: int | None = None
    phase_state: int | None = None
    phase_flags: int = 0
    phase_cycles: int | None = None
    event_seq: int = 0xFFFFFFFF
    verify_state: int | None = None
    verify_reason: int | None = None
    stable_count: int = 0
    feedback_age_us: int = 0xFFFFFFFF
    calibration_status: str = ''


@dataclass(frozen=True)
class BoardLink:
    address: tuple
    session: int
    profile_crc: str
    controller_budget: int
    fpga_budget: int
    protocol_version: int = 1
    build_id: int | None = None


@dataclass(frozen=True)
class AuthoritySnapshot:
    sampled_mono_s: float
    session: int
    values: dict


class Channel:
    def __init__(self):
        self.control = None
        self.board = None
        self.board_history = ()
        self.board_link = None
        self.authority = None


_active = None


def install():
    global _active
    channel = Channel()
    previous = _active
    _active = channel
    return channel, previous


def uninstall(token):
    global _active
    channel, previous = token
    if _active is channel:
        _active = previous


def channel():
    return _active


def sample():
    current = _active
    return (current.control, current.board) if current is not None else (None, None)


def publish_board(record, owner, run_id=None):
    """Caller supplies only a matched FPG1 task reply, never a startup probe."""
    try:
        if owner is None or owner is not _active:
            return
        if run_id is not None and (owner.control is None or owner.control.run_id != run_id):return
        if owner.board_link is not None and owner.board_link.session != int(record['session']):return
        if record['kind'] not in ('task_sample','authority_sample') or (record['kind']=='task_sample' and not record['dispatched']):
            return
        previous = owner.board
        same = previous is not None and previous.session == int(record['session'])
        match = bool(record['reference_match'])
        violations = int(record['violations'])
        value = BoardSnapshot(time.monotonic(), int(record['session']),
            int(record['sequence']), int(record['step']), match, violations,
            events=int(record.get('events',0)),phase=int(record.get('phase',0)),
            round_trip_ms=float(record.get('host_round_trip_us',0))/1000,
            board_processing_ms=float(record.get('board_processing_us',0))/1000,
            checked_count=(previous.checked_count if same else 0)+1,
            reference_matches=(previous.reference_matches if same else 0)+int(match),
            reference_mismatches=(previous.reference_mismatches if same else 0)+int(not match),
            violation_count=(previous.violation_count if same else 0)+int(bool(violations)),
            protocol_version=int(record.get('protocol_version',1)),build_id=record.get('build_id'),
            phase_state=record.get('phase_state'),phase_flags=int(record.get('phase_flags',0)),
            phase_cycles=record.get('phase_cycles'),event_seq=int(record.get('event_seq',0xFFFFFFFF)),
            verify_state=record.get('verify_state'),feedback_age_us=int(record.get('feedback_age_us',0xFFFFFFFF)),
            calibration_status=str(record.get('calibration_status','')),
            verify_reason=previous.verify_reason if same else None,stable_count=previous.stable_count if same else 0)
        owner.board = value
        owner.board_history = ((owner.board_history if same else ())+(value,))[-32:]
    except Exception:
        pass  # Display delivery must not change the FPGA client's result.


class RunTelemetry:
    """One policy run, created independently of camera/UI sampling frequency."""
    def __init__(self, *, execute, max_actions, max_authority):
        self.owner = _active
        now = time.monotonic()
        self.value = ControlSnapshot(uuid.uuid4().hex, now, now,
            execute=bool(execute), max_actions=max_actions, max_authority=max_authority)
        if self.owner is not None:
            self.owner.board = None
            self.owner.board_history = ()
            self.owner.board_link = None
            self.owner.authority = None
        self._publish()

    def _publish(self):
        try:
            if self.owner is not None and self.owner is _active:
                self.owner.control = self.value
        except Exception:
            pass

    def state(self, state, detail=''):
        try:
            now = time.monotonic()
            self.value = replace(self.value, sampled_mono_s=now, run_state=state,
                elapsed_s=max(0., now-self.value.started_mono_s), detail=str(detail)[:160])
            if state == 'running':
                self.value = replace(self.value, started_mono_s=now, elapsed_s=0.)
            self._publish()
        except Exception:
            pass

    def tick(self, *, step, authority, max_correction_rad, source, dispatched):
        if self.owner is None:return
        try:
            now = time.monotonic()
            a, delta = float(authority), float(max_correction_rad)
            if not math.isfinite(a) or not 0 <= a <= 1 or not math.isfinite(delta) or delta < 0:
                return
            sent = bool(self.value.execute and dispatched)
            self.value = replace(self.value, sampled_mono_s=now, step=int(step), authority=a,
                max_correction_rad=delta, source=str(source), run_state='running',
                elapsed_s=max(0., now-self.value.started_mono_s),
                dispatched_steps=self.value.dispatched_steps+int(sent),
                corrected_steps=self.value.corrected_steps+int(sent and delta > 0.0001),
                candidate_steps=self.value.candidate_steps+int(sent and source == 'SmolVLA'))
            self._publish()
        except Exception:
            pass


class _NoTelemetry:
    def state(self, *args, **kwargs):pass
    def tick(self, *args, **kwargs):pass


def create_run(**kwargs):
    if _active is None:
        return _NoTelemetry()
    try:
        return RunTelemetry(**kwargs)
    except Exception as error:
        import logging
        logging.getLogger(__name__).warning('Dashboard telemetry unavailable: %s', error)
        return _NoTelemetry()


def publish_link(metadata, session, owner, run_id=None):
    try:
        if owner is not None and owner is _active:
            if run_id is not None and (owner.control is None or owner.control.run_id != run_id):return
            owner.board_link = BoardLink(tuple(metadata['address']),int(session),
                str(metadata['profile_crc']),int(metadata['controller_budget']),int(metadata['fpga_budget']),
                int(metadata.get('protocol_version',1)),metadata.get('build_id'))
    except Exception:
        pass


def sample_display():
    owner = _active
    return dict(control=owner.control,board=owner.board,board_history=owner.board_history,
        board_link=owner.board_link,authority=owner.authority) if owner is not None else {}


def publish_authority(values,session,owner,run_id=None):
    try:
        if owner is None or owner is not _active:return
        if run_id is not None and (owner.control is None or owner.control.run_id!=run_id):return
        if owner.board_link is not None and owner.board_link.session!=session:return
        owner.authority=AuthoritySnapshot(time.monotonic(),session,dict(values))
    except Exception:pass


def publish_vision(session,event_seq,values,owner,run_id=None):
    """Update only a matched v3 session; never changes task counters."""
    try:
        if owner is None or owner is not _active or owner.board is None:return
        if run_id is not None and (owner.control is None or owner.control.run_id!=run_id):return
        old=owner.board
        if old.session!=session or old.protocol_version!=3:return
        if old.event_seq not in (0xFFFFFFFF,event_seq):return
        frame,state,reason,count=values
        owner.board=replace(old,verify_state=int(state),verify_reason=int(reason),
                            stable_count=int(count),event_seq=int(event_seq))
    except Exception:pass
