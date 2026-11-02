"""Validate one display-status publisher; no guard register access."""
from host import task_status


class Inbox:
    def __init__(self,journal):
        self.journal=journal
        self.identity=None
        self.sequence=self.tick=-1

    def handle(self,packet,address,received_ns):
        kind,session,sequence,tick,values=task_status.unpack(packet)
        identity=(address,session)
        if kind!=1: raise ValueError('expected task request')
        if self.identity is None:
            if sequence!=0: raise ValueError('first task sequence must be zero')
        elif identity!=self.identity:
            raise ValueError('different task publisher/session; restart service')
        if sequence<=self.sequence or tick<=self.tick:
            raise ValueError('old task sequence/tick')
        self.journal.write('task_status_received',peer=list(address),session=session,sequence=sequence,
                           sender_tick_us=tick,board_received_ns=received_ns,
                           claim_only=True,**values)
        self.identity=identity
        self.sequence,self.tick=sequence,tick
        return task_status.pack(129,session,sequence,tick,values)
