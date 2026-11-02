"""Read-only Linux camera identity/format inventory, using only the standard library.

No STREAMON, capture, format-setting or CAN calls. UAPI layout: linux/videodev2.h.
Reference: kernel.org/doc/html/latest/userspace-api/media/v4l/vidioc-enum-fmt.html
and vidioc-enum-framesizes.html. Intended for Linux x86_64 Cobot.
"""
import errno
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess

QUERYCAP = 0x80685600
ENUM_FMT = 0xC0405602
ENUM_FRAMESIZES = 0xC02C564A
RGB_FOURCC = {'MJPG', 'YUYV', 'UYVY', 'RGB3', 'BGR3'}


def supports_size(size, width=640, height=480):
    if size['type'] == 'discrete':
        return size['width'] == width and size['height'] == height
    for axis, value in [('width', width), ('height', height)]:
        lo, hi, step = size['min_'+axis], size['max_'+axis], size['step_'+axis]
        if not lo <= value <= hi or (step > 0 and (value-lo) % step):
            return False
    return True


def query_formats(node, ioctl=None):
    if ioctl is None:
        import fcntl
        ioctl = fcntl.ioctl
    result = dict(formats=[], rgb_640x480=False, query_error=None)
    fd = None
    try:
        fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
        cap = bytearray(104)
        ioctl(fd, QUERYCAP, cap, True)
        cstring = lambda data: bytes(data).split(b'\0', 1)[0].decode(errors='replace')
        result.update(driver=cstring(cap[:16]), card=cstring(cap[16:48]), bus=cstring(cap[48:80]))
        caps, device_caps = struct.unpack_from('=II', cap, 84)
        result['capabilities'] = hex(device_caps if caps & 0x80000000 else caps)
        for index in range(256):
            fmt = bytearray(64)
            struct.pack_into('=II', fmt, 0, index, 1)  # VIDEO_CAPTURE, not metadata/output
            try:
                ioctl(fd, ENUM_FMT, fmt, True)
            except OSError as exc:
                if exc.errno == errno.EINVAL:
                    break
                raise
            pixel = struct.unpack_from('=I', fmt, 44)[0]
            fourcc = pixel.to_bytes(4, 'little').decode('ascii', errors='replace')
            sizes = []
            size_error = None
            for size_index in range(256):
                raw = bytearray(44)
                struct.pack_into('=II', raw, 0, size_index, pixel)
                try:
                    ioctl(fd, ENUM_FRAMESIZES, raw, True)
                except OSError as exc:
                    if exc.errno != errno.EINVAL:
                        raise
                    if size_index == 0:
                        size_error = 'No enumerated sizes (EINVAL); not evidence that camera is absent'
                    break
                kind = struct.unpack_from('=I', raw, 8)[0]
                if kind == 1:
                    w, h = struct.unpack_from('=II', raw, 12)
                    sizes.append(dict(type='discrete', width=w, height=h))
                elif kind in (2, 3):
                    values = struct.unpack_from('=6I', raw, 12)
                    sizes.append(dict(type='continuous' if kind == 2 else 'stepwise',
                        **dict(zip(['min_width', 'max_width', 'step_width', 'min_height', 'max_height', 'step_height'], values))))
                    break
                else:
                    raise ValueError('Unexpected V4L2 frame-size type '+str(kind))
            color = fourcc in RGB_FOURCC and any(supports_size(s) for s in sizes)
            result['formats'].append(dict(fourcc=fourcc, description=cstring(fmt[12:44]),
                                           sizes=sizes, size_error=size_error, rgb_640x480=color))
            result['rgb_640x480'] |= color
    except (OSError, ValueError) as exc:
        result['query_error'] = f'{type(exc).__name__}: {exc}'
    finally:
        if fd is not None:
            os.close(fd)
    return result


def serial_ancestors(device):
    found = []
    for path in (device, *device.parents):
        serial = path/'serial'
        if serial.is_file():
            found.append(dict(path=str(serial), value=serial.read_text().strip()))
    return found


def scan_cameras():
    rows = []
    links = list(Path('/dev/v4l/by-id').glob('*'))
    for path in sorted(Path('/sys/class/video4linux').glob('video*'), key=lambda p: int(p.name[5:])):
        node = Path('/dev')/path.name
        device = (path/'device').resolve()
        properties = {}
        udev_error = None
        if shutil.which('udevadm'):
            try:
                proc = subprocess.run(['udevadm', 'info', '--query=property', '--name='+str(node)],
                                      capture_output=True, text=True, timeout=5)
                properties = dict(line.split('=', 1) for line in proc.stdout.splitlines() if '=' in line)
                if proc.returncode:
                    udev_error = proc.stderr.strip() or 'udevadm exit '+str(proc.returncode)
            except (OSError, subprocess.TimeoutExpired) as exc:
                udev_error = str(exc)
        else:
            udev_error = 'udevadm unavailable; using sysfs/by-id only'
        rows.append(dict(node=str(node), sysfs_device=str(device),
                         serial_ancestors=serial_ancestors(device),
                         udev={k: properties[k] for k in ['ID_SERIAL', 'ID_SERIAL_SHORT', 'ID_PATH', 'ID_V4L_PRODUCT'] if k in properties},
                         udev_error=udev_error,
                         by_id=[p.name for p in links if p.resolve() == node], **query_formats(node)))
    return rows


def match_roles(rows, camera_roles):
    matches = {role: [] for role in camera_roles}
    for row in rows:
        identities = [p['value'] for p in row['serial_ancestors']]
        identities += [row['udev'][k] for k in ('ID_SERIAL_SHORT', 'ID_SERIAL') if k in row['udev']] + row['by_id']
        for role, serial in camera_roles.items():
            # Serial match, never default-index fallback or digit-substring guessing.
            pattern = r'(?<![0-9])'+re.escape(serial)+r'(?![0-9])'
            if row['rgb_640x480'] and any(re.search(pattern, text) for text in identities):
                matches[role].append(int(Path(row['node']).name[5:]))
    return matches


if __name__ == '__main__':
    print(json.dumps(scan_cameras(), indent=2, ensure_ascii=False))
