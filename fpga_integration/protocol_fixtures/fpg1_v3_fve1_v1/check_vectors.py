#!/usr/bin/env python3
"""Offline wire conformance examples for the frozen design; no hardware imports."""
import argparse
import json
import math
from pathlib import Path
import struct
import zlib

HEADER = struct.Struct('<4sBBHIIQ')
CRC = struct.Struct('<I')
LAYOUTS = {
    ('FPG1', 3, 1): struct.Struct('<IIIQII84i'),
    ('FPG1', 3, 2): struct.Struct('<10I'),
    ('FVE1', 1, 1): struct.Struct('<QI6H'),
    ('FVE1', 1, 2): struct.Struct('<4I'),
    ('FVE1', 1, 3): struct.Struct('<'),
    ('FVE1', 1, 4): struct.Struct('<4I'),
}
UNKNOWN = 0xffffffff
SESSION = 0x10203040
BUILD_ID = 0x46504734
HERE = Path(__file__).resolve().parent


def crc_bitwise(data):
    """Independent CRC-32/ISO-HDLC calculation to check zlib output."""
    value = 0xffffffff
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xedb88320 if value & 1 else 0)
    return value ^ 0xffffffff


def wire(magic, version, kind, session, sequence, tick, values):
    payload = LAYOUTS[(magic, version, kind)].pack(*values)
    body = HEADER.pack(magic.encode('ascii'), version, kind, len(payload),
                       session, sequence, tick) + payload
    return body + CRC.pack(zlib.crc32(body) & UNKNOWN)


def decode(packet):
    """Validate wire and self-contained fields; no session or state emulation."""
    if len(packet) < 28:
        raise ValueError('short_frame')
    magic, version, kind, size, session, sequence, tick = HEADER.unpack_from(packet)
    try:
        key = (magic.decode('ascii'), version, kind)
    except UnicodeError:
        raise ValueError('unsupported_header') from None
    if key not in LAYOUTS:
        raise ValueError('unsupported_header')
    layout = LAYOUTS[key]
    if size != layout.size or len(packet) != 28 + size:
        raise ValueError('bad_length')
    if CRC.unpack_from(packet, len(packet) - 4)[0] != crc_bitwise(packet[:-4]):
        raise ValueError('bad_crc')
    if not 1 <= session <= 0xfffffffe:
        raise ValueError('bad_session')
    if sequence == UNKNOWN:
        raise ValueError('bad_sequence')
    values = layout.unpack(packet[24:-4])
    if key == ('FPG1', 3, 1):
        profile, step, flags, elapsed, age, depth = values[:6]
        if flags & ~15:
            raise ValueError('reserved_request_flags')
        if step == UNKNOWN:
            if flags != 0 or elapsed != 0 or sequence != 0:
                raise ValueError('bad_probe')
        elif not flags & 4 or sequence == 0:
            raise ValueError('bad_task')
    elif key == ('FPG1', 3, 2):
        profile, violations, events, scheduled, board_us, stage, flags, cycles, build, event = values
        if violations & ~0x7ff or events & ~0xf or flags & ~0x30f:
            raise ValueError('reserved_response_bits')
        if scheduled not in (0, 3, 4) or stage not in (0, 1, 2, 3):
            raise ValueError('bad_enum')
        if board_us == UNKNOWN or cycles == UNKNOWN or build != BUILD_ID:
            raise ValueError('bad_result_identity')
        valid, changed = bool(flags & 4), bool(flags & 8)
        pending = bool(flags & 2)
        verify = (flags >> 8) & 3
        if (changed and not valid) or (flags & 1 and not (valid and changed and stage == 3)):
            raise ValueError('inconsistent_stage_flags')
        if pending != (verify == 1) or pending != (event != UNKNOWN):
            raise ValueError('inconsistent_event_flags')
    elif key == ('FVE1', 1, 1):
        if values[-1] & ~1:
            raise ValueError('reserved_feature_flags')
        if values[1] == UNKNOWN:
            raise ValueError('bad_frame_id')
    elif key[0] == 'FVE1' and kind in (2, 4):
        frame, state, reason, count = values
        if state > 3 or reason > 9 or count > 255:
            raise ValueError('bad_enum')
    return dict(magic=key[0], version=version, kind=kind, session=session,
                sequence=sequence, tick_us=tick, values=list(values))


def build_fixture():
    vectors = []

    def add(name, magic, version, kind, seq, tick, values, note):
        packet = wire(magic, version, kind, SESSION, seq, tick, values)
        vectors.append(dict(name=name, note=note, expected_length=len(packet),
                            crc32=f'{zlib.crc32(packet[:-4]) & UNKNOWN:08x}',
                            hex=packet.hex(), decoded=decode(packet)))
        return packet

    # Signed joint values and gripper micrometres, for encoding only.
    q = [0, 1730000, -896000, -158985, 59100, 135610, 6300,
         -40453, 1730000, -896000, 10187, 117154, -91755, 3400]
    add('fpg1_task_signed', 'FPG1', 3, 1, 622, 21500000,
        [0x8f4c7f55, 621, 4, 21080000, 12000, UNKNOWN] + q * 4 + [0] * 28,
        'Encoding fixture only; q is not a measured on-board run.')
    add('fpg1_fold_entered', 'FPG1', 3, 2, 202, 7000000,
        [0x8f4c7f55, 0, 0, 3, 2200, 3, 0x0d, 7, BUILD_ID, UNKNOWN],
        'Fold transition: valid and changed, no look event yet; cycles=7 is synthetic.')
    pending = add('fpg1_look_pending', 'FPG1', 3, 2, 622, 21500000,
                  [0x8f4c7f55, 0, 1, 4, 2200, 3, 0x106, 7, BUILD_ID, 621],
                  'Header sequence=622; original event sequence remains 621.')
    feature = add('fve1_feature', 'FVE1', 1, 1, 621, 21594658,
                  [21080000, 265, 4290, 370, 361, 9000, 1500, 1],
                  'Original design example; shape values are synthetic, not field calibration.')
    assert zlib.crc32(feature[:-4]) & UNKNOWN == 0x6b789ea8
    add('fve1_wait_stable', 'FVE1', 1, 2, 621, 21594658, [265, 1, 5, 1],
        'After consuming first eligible shape frame.')
    add('fve1_query', 'FVE1', 1, 3, 621, 22000000, [], 'Read-only query.')
    add('fve1_query_observed', 'FVE1', 1, 4, 621, 22000000, [267, 2, 0, 3],
        'Synthetic terminal query result; does not imply a real completed run.')
    add('fve1_query_unconfirmed', 'FVE1', 1, 4, 621, 23000000, [265, 3, 8, 0],
        'Alternative timeout scenario, independent from observed fixture.')

    negatives = []

    def bad(name, packet, error):
        negatives.append(dict(name=name, hex=packet.hex(), expected_error=error))

    def patch(packet, offset, raw):
        body = bytearray(packet[:-4])
        body[offset:offset + len(raw)] = raw
        return bytes(body) + CRC.pack(zlib.crc32(body) & UNKNOWN)

    bad('truncated', pending[:-1], 'bad_length')
    bad('trailing_byte', pending + b'\x00', 'bad_length')
    damaged = bytearray(pending)
    damaged[40] ^= 1
    bad('crc_corruption', bytes(damaged), 'bad_crc')
    bad('old_version', patch(pending, 4, b'\x01'), 'unsupported_header')
    old_body = HEADER.pack(b'FPG1', 1, 2, 20, SESSION, 622, 21500000)
    old_body += struct.pack('<5I', 0x8f4c7f55, 0, 1, 4, 2200)
    bad('old_48_byte_reply', old_body + CRC.pack(zlib.crc32(old_body) & UNKNOWN),
        'unsupported_header')
    bad('unsupported_kind', patch(pending, 5, b'\x03'), 'unsupported_header')
    bad('reserved_reply_flag', patch(pending, 48, struct.pack('<I', 0x116)),
        'reserved_response_bits')
    bad('missing_pending_event', patch(pending, 60, struct.pack('<I', UNKNOWN)),
        'inconsistent_event_flags')
    bad('changed_without_valid', patch(pending, 48, struct.pack('<I', 0x10a)),
        'inconsistent_stage_flags')
    bad('bad_build_id', patch(pending, 56, struct.pack('<I', 0x46504732)),
        'bad_result_identity')
    bad('reserved_feature_flag', patch(feature, 46, struct.pack('<H', 3)),
        'reserved_feature_flags')
    bad('unknown_frame_id', patch(feature, 32, struct.pack('<I', UNKNOWN)),
        'bad_frame_id')
    return dict(schema='fpg1_v3_fve1_v1_wire_vectors_1_1',
                spec='../../fpga_fpg1_v3_fve1_v1_protocol_zh.md',
                qualification='Wire fixtures only; no RTL, timing or hardware verification.',
                positive=vectors, negative=negatives)


def verify(fixture):
    assert fixture['schema'] == 'fpg1_v3_fve1_v1_wire_vectors_1_1'
    for item in fixture['positive']:
        packet = bytes.fromhex(item['hex'])
        assert len(packet) == item['expected_length'], item['name']
        assert decode(packet) == item['decoded'], item['name']
        assert f'{crc_bitwise(packet[:-4]):08x}' == item['crc32'], item['name']
        fields = item['decoded']
        rebuilt = wire(fields['magic'], fields['version'], fields['kind'], fields['session'],
                       fields['sequence'], fields['tick_us'], fields['values'])
        assert rebuilt == packet, item['name']
    for item in fixture['negative']:
        try:
            decode(bytes.fromhex(item['hex']))
        except ValueError as error:
            assert str(error) == item['expected_error'], (item['name'], str(error))
        else:
            raise AssertionError('Accepted negative fixture: ' + item['name'])
    # Public field offsets and signed decoding, independent of round-trip result.
    p = bytes.fromhex(fixture['positive'][0]['hex'])
    assert len(p) == 392 and struct.unpack_from('<i', p, 24 + 28 + 2 * 4)[0] == -896000
    pending = bytes.fromhex(fixture['positive'][2]['hex'])
    assert struct.unpack_from('<I', pending, 12)[0] == 622
    assert struct.unpack_from('<I', pending, 60)[0] == 621
    assert crc_bitwise(b'123456789') == 0xcbf43926
    for value, expected in ((0.0000005, 1), (-0.0000005, -1),
                            (0.0063, 6300), (-0.896, -896000)):
        encoded = int(math.copysign(math.floor(abs(value) * 1000000 + 0.5), value))
        assert encoded == expected
    assert len(LAYOUTS) == 6


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-fixtures', action='store_true',
                        help='Create a new golden_packets.json; refuses to overwrite.')
    args = parser.parse_args()
    path = HERE / 'golden_packets.json'
    if args.write_fixtures:
        fixture = build_fixture()
        verify(fixture)
        with path.open('x', encoding='utf-8') as output:
            json.dump(fixture, output, ensure_ascii=False, indent=2)
            output.write('\n')
    fixture = json.loads(path.read_text(encoding='utf-8'))
    verify(fixture)
    print(f"WIRE_CONFORMANCE_PASSED positive={len(fixture['positive'])} "
          f"negative={len(fixture['negative'])} layouts={len(LAYOUTS)} hardware_used=false")


if __name__ == '__main__':
    main()
