"""Independent diagnostic events; FPG1 bytes and meanings stay unchanged."""
import struct
import zlib

from .protocol import REQUEST, Sample

MAGIC = b'FWD1'
HEADER = struct.Struct('<4sBBHIIQ')
CRC = struct.Struct('<I')
MODE = struct.Struct('<I')
REPLY = struct.Struct('<IIIIQQIII')
BEGIN, ALLOW, HOLD, PAUSE, RESUME, STATUS, END = range(1, 8)
PERIODIC, ON_RESULT = 1, 2
IDLE, ACTIVE, PAUSED, RESULT_ONLY, ENDED = range(5)
NAMES = {BEGIN: 'begin', ALLOW: 'allow', HOLD: 'hold', PAUSE: 'pause',
         RESUME: 'resume', STATUS: 'status', END: 'end'}
REPLY_FIELDS = ('build_id', 'status', 'timeout_count', 'state', 'age_cycles',
                'last_gap_cycles', 'violations', 'events', 'phase')


def _packet(op, session, sequence, tick_us, payload):
    body = HEADER.pack(MAGIC, 1, op, len(payload), session, sequence, tick_us) + payload
    return body + CRC.pack(zlib.crc32(body) & 0xffffffff)


def unpack(data, response=False):
    if len(data) < HEADER.size + CRC.size:
        raise ValueError('Truncated diagnostic frame')
    magic, version, op, length, session, sequence, tick = HEADER.unpack_from(data)
    command = op & 0x7f
    if magic != MAGIC or version != 1 or command not in NAMES or bool(op & 0x80) != response:
        raise ValueError('Invalid diagnostic header')
    if response:
        wanted = REPLY.size
    elif command == BEGIN:
        wanted = MODE.size
    elif command in (ALLOW, HOLD):
        wanted = REQUEST.size
    else:
        wanted = 0
    if length != wanted or len(data) != HEADER.size + wanted + CRC.size:
        raise ValueError('Invalid diagnostic frame size')
    if zlib.crc32(data[:-4]) & 0xffffffff != CRC.unpack_from(data, len(data)-4)[0]:
        raise ValueError('Diagnostic CRC mismatch')
    payload = data[HEADER.size:-4]
    if not response:
        if command == BEGIN and MODE.unpack(payload)[0] not in (PERIODIC, ON_RESULT):
            raise ValueError('Invalid stream mode')
        if command in (ALLOW, HOLD):
            Sample.unpack(payload)
    return command, session, sequence, tick, payload


def request(op, session, sequence, tick_us, payload=b''):
    result = _packet(op, session, sequence, tick_us, payload)
    unpack(result)
    return result


def response(op, session, sequence, tick_us, values):
    return _packet(op | 0x80, session, sequence, tick_us,
                  REPLY.pack(*(values[name] for name in REPLY_FIELDS)))


def read_response(data):
    op, session, sequence, tick, payload = unpack(data, response=True)
    return op, session, sequence, tick, dict(zip(REPLY_FIELDS, REPLY.unpack(payload)))
