"""Authenticated, non-actuator FPGA lifecycle messages."""
import hashlib
import hmac
import json
import secrets


def _signature(key, kind, nonce, value):
    msg = ('FPGA-LIFECYCLE-1|' + kind + '|' + nonce + '|' + value).encode('ascii')
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def make_message(key, kind, value):
    nonce = secrets.token_hex(16)
    return dict(version=1, kind=kind, nonce=nonce, value=value,
                mac=_signature(key, kind, nonce, value))


def reply(key, request, value):
    return dict(version=1, kind='reply', nonce=request['nonce'], value=value,
                mac=_signature(key, 'reply', request['nonce'], value))


def check_message(key, packet, kinds):
    if not isinstance(packet, dict) or set(packet) != {'version','kind','nonce','value','mac'}:
        raise ValueError('invalid fields')
    if packet['version'] != 1 or packet['kind'] not in kinds:
        raise ValueError('invalid kind')
    nonce, value, mac = packet['nonce'], packet['value'], packet['mac']
    if not isinstance(nonce,str) or len(nonce)!=32 or not all(c in '0123456789abcdef' for c in nonce):
        raise ValueError('invalid nonce')
    if not isinstance(value,str) or len(value)>100 or not value.isascii() or not isinstance(mac,str):
        raise ValueError('invalid value')
    if not hmac.compare_digest(mac, _signature(key,packet['kind'],nonce,value)):
        raise ValueError('bad authentication')
    return packet


def dumps(obj):
    return json.dumps(obj,separators=(',',':')).encode('ascii')
