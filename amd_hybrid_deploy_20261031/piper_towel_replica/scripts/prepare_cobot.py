"""Map this Cobot's USB identities to CAN and RGB V4L2 nodes, not author indices."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT/'configs/cobot_site.json'


def usb_serial(device):
    for path in (device, *device.parents):
        serial = path/'serial'
        if serial.is_file():
            return serial.read_text().strip()
    return None


def rgb_formats(text):
    return bool(re.search(r"'(MJPG|YUYV|UYVY|RGB3|BGR3)'", text)) and '640x480' in text


def color_candidate(row):
    if row.get('query_error') or not row['rgb_640x480']:
        return False
    # D435 stereo/IR nodes can advertise UYVY as well as grayscale/interleaved IR.
    # A color-like FourCC alone does not identify the physical RGB sensor.
    fourcc = {f['fourcc'].strip() for f in row['formats']}
    is_realsense = 'realsense' in (row.get('card', '')+' '+row.get('udev', {}).get('ID_V4L_PRODUCT', '')).lower()
    if is_realsense and fourcc.intersection({'GREY', 'Y8I', 'Y12I', 'Z16'}):
        return False
    return True


def explicit_camera_roles(inventory, indices):
    if len(set(indices)) != 3:
        raise ValueError('Three physical RGB cameras must have distinct nodes')
    nodes = {int(Path(r['node']).name[5:]): r for r in inventory}
    serials = {}
    cameras = {}
    for role, index in zip(('left', 'middle', 'right'), indices, strict=True):
        row = nodes.get(index)
        if row is None or not color_candidate(row):
            raise ValueError(f'{role}: video{index} is missing, failed query or is not a color sensor')
        serial = row.get('udev', {}).get('ID_SERIAL_SHORT')
        if not serial:
            serial = next((p['value'] for p in row['serial_ancestors'] if re.fullmatch(r'[0-9]{12}', p['value'])), None)
        if not serial:
            raise ValueError(f'video{index}: no USB identity; cannot persist role by index alone')
        serials[role], cameras[role] = serial, index
    if len(set(serials.values())) != 3:
        raise ValueError('Selected nodes do not belong to three distinct physical cameras')
    return serials, cameras


def save_previews(cameras):
    sys.path.insert(0, str(ROOT/'runtime_source/src'))
    import cv2
    from lerobot.cameras.opencv import OpenCVCamera, OpenCVCameraConfig
    dest = ROOT/'logs/camera_preview'
    dest.mkdir(parents=True, exist_ok=True)
    for role, index in cameras.items():
        camera = OpenCVCamera(OpenCVCameraConfig(index_or_path=index, width=640, height=480, fps=30))
        try:
            camera.connect()
            frame = camera.async_read(timeout_ms=2000)
            if not cv2.imwrite(str(dest/(role+'.jpg')), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)):
                raise RuntimeError('Could not save '+role)
        finally:
            if camera.is_connected:
                camera.disconnect()
    print('RGB previews:', dest)


def discover(site, indices=None):
    from camera_inventory import scan_cameras, match_roles
    ports = {}
    for path in sorted(Path('/sys/class/net').glob('can*')):
        serial = usb_serial((path/'device').resolve())
        for side, expected in site['usb_serials'].items():
            if serial == expected:
                if side in ports:
                    raise ValueError('Duplicate CAN identity: '+side)
                ports[side] = path.name
    if set(ports) != {'left', 'right'}:
        raise ValueError('Expected both arm USB adapters; found '+str(ports))
    inventory = scan_cameras()
    dest = ROOT/'logs'
    dest.mkdir(parents=True, exist_ok=True)
    (dest/'camera_inventory.json').write_text(json.dumps(inventory, indent=2, ensure_ascii=False), encoding='utf-8')
    if indices is not None:
        serials, cameras = explicit_camera_roles(inventory, indices)
        site.setdefault('camera_roles_previous', site['camera_roles'].copy())
        site['camera_roles'] = serials
        site['camera_role_confirmation'] = 'operator supplied left/middle/right indices after visual preview'
        return ports, cameras
    cameras = match_roles([r for r in inventory if color_candidate(r)], site['camera_roles'])
    if any(len(nodes) != 1 for nodes in cameras.values()):
        raise ValueError('Need one RGB 640x480 node per serial; candidates='+str(cameras)+
                         '; details saved to logs/camera_inventory.json (no v4l-utils needed)')
    return ports, {r: nodes[0] for r, nodes in cameras.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--configure-can', action='store_true', help='Use sudo ip to activate only the two identified arm adapters at 1Mbps; no robot commands')
    parser.add_argument('--preview', action='store_true', help='Open ONLY RGB cameras and save images for role confirmation; no CAN instance')
    parser.add_argument('--diagnose', action='store_true', help='Only read camera identities/formats; no CAN check/configuration, capture or profile edits')
    parser.add_argument('--preview-nodes', type=int, nargs='+', help='Preview explicit color nodes by video number only; no CAN or profile changes')
    parser.add_argument('--camera-indices', type=int, nargs=3, metavar=('LEFT', 'MIDDLE', 'RIGHT'), help='Bind visually confirmed views, persisting their USB identities')
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('Run on Cobot Linux')
    if args.diagnose:
        if args.preview or args.configure_can or args.preview_nodes is not None or args.camera_indices is not None:
            parser.error('diagnose cannot be combined with preview/configure-can')
        from camera_inventory import scan_cameras, match_roles
        site = json.loads(SITE.read_text())
        inventory = scan_cameras()
        (ROOT/'logs').mkdir(parents=True, exist_ok=True)
        report = ROOT/'logs/camera_inventory.json'
        report.write_text(json.dumps(inventory, indent=2, ensure_ascii=False), encoding='utf-8')
        print(json.dumps(dict(expected=site['camera_roles'], candidates=match_roles(inventory, site['camera_roles'])), ensure_ascii=False))
        for row in inventory:
            print(json.dumps(dict(node=row['node'], card=row.get('card'),
                                  serials=[p['value'] for p in row['serial_ancestors']],
                                  udev=row['udev'], by_id=row['by_id'],
                                  color_sensor_candidate=color_candidate(row),
                                  formats=[dict(fourcc=f['fourcc'], rgb_640x480=f['rgb_640x480'], size_error=f['size_error']) for f in row['formats']],
                                  error=row['query_error']), ensure_ascii=False))
        print('CAMERA_DIAGNOSE_ONLY:', report, '(no CAN, capture, config edit, apt or pip changes)')
        return
    if args.preview_nodes is not None:
        if args.preview or args.configure_can or args.camera_indices is not None:
            parser.error('preview-nodes is camera-only; do not combine with binding/configure-can')
        if len(set(args.preview_nodes)) != len(args.preview_nodes):
            parser.error('preview-nodes must be distinct')
        from camera_inventory import scan_cameras
        nodes = {int(Path(r['node']).name[5:]): r for r in scan_cameras()}
        for index in args.preview_nodes:
            row = nodes.get(index)
            if row is None or not color_candidate(row):
                raise ValueError(f'video{index}: not a verified color-sensor candidate')
            print('PREVIEW_NODE:', row['node'], row['udev'].get('ID_SERIAL_SHORT'))
        save_previews({'video'+str(i): i for i in args.preview_nodes})
        print('PREVIEW_NODES_ONLY: no CAN, robot enable/targets or profile edits')
        return
    site = json.loads(SITE.read_text())
    ports, cameras = discover(site, args.camera_indices)
    # Preserve saved canonical names; prepare_day restores them by USB identity.
    site['camera_indices'] = cameras
    # A later CAN/camera rediscovery must not undo an explicit model selection.
    models = site.setdefault('models', {})
    for role, default in {'act': 'models/act060000', 'smolvla': 'models/smol005000'}.items():
        if not models.get(role):
            models[role] = default
    site['training_start_pose'] = 'deployment_assets/task_start.json'
    own_start = json.loads((ROOT/'deployment_assets/task_start.json').read_text())
    site['max_actions'] = own_start['task_frames']
    site['note'] = 'Local adaptation of pinned reference; indices resolved from serial and RGB formats. Visually confirm previews.'
    if args.configure_can:
        for name in ports.values():
            details = subprocess.check_output(['ip', '-details', 'link', 'show', name], text=True)
            if 'bitrate 1000000 ' in details and re.search(r'<[^>]*\bUP\b', details):
                print(name, 'already UP at 1Mbps; unchanged')
                continue
            for command in (['down'], ['type', 'can', 'bitrate', '1000000'], ['up']):
                subprocess.run(['sudo', 'ip', 'link', 'set', name, *command], check=True)
    SITE.write_text(json.dumps(site, indent=2), encoding='utf-8')
    print(json.dumps(dict(can=ports, rgb_indices=cameras, config=str(SITE)), ensure_ascii=False))
    if args.preview:
        save_previews(cameras)


if __name__ == '__main__':
    main()
