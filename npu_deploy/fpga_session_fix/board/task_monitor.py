"""Diagnostic sessions expire after inactivity; they never authorize motion."""
from host import task_status


class SessionLease:
    """One publisher at a time; replacement requires 5 seconds of silence."""
    def __init__(self, idle_ns=5_000_000_000):
        self.idle_ns = idle_ns
        self.identity = None
        self.sequence = self.tick = -1
        self.received_ns = None

    def check(self, identity, sequence, tick, received_ns):
        if self.identity is None:
            if sequence != 0:
                raise ValueError('first sequence must be zero')
        elif identity != self.identity:
            if received_ns - self.received_ns < self.idle_ns:
                raise ValueError('different session; previous publisher still active')
            if sequence != 0:
                raise ValueError('first sequence must be zero')
        elif sequence <= self.sequence or tick <= self.tick:
            raise ValueError('old sequence/tick')

    def commit(self, identity, sequence, tick, received_ns):
        replaced = self.identity is not None and self.identity != identity
        self.identity, self.sequence, self.tick, self.received_ns = identity, sequence, tick, received_ns
        return replaced


class Inbox:
    def __init__(self, journal):
        self.journal = journal
        self.lease = SessionLease()

    def handle(self, packet, address, received_ns):
        kind, session, sequence, tick, values = task_status.unpack(packet)
        identity = (address, session)
        if kind != 1:
            raise ValueError('expected task request')
        self.lease.check(identity, sequence, tick, received_ns)
        self.journal.write('task_status_received', peer=list(address), session=session,
                           sequence=sequence, sender_tick_us=tick, board_received_ns=received_ns,
                           claim_only=True, **values)
        if self.lease.commit(identity, sequence, tick, received_ns):
            self.journal.write('task_session_replaced_after_idle', peer=list(address), session=session)
        return task_status.pack(129, session, sequence, tick, values)
