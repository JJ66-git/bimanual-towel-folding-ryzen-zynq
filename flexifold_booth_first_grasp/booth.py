"""Serve the FlexiFold booth page and send diagnostic FPG1 samples."""
import argparse
from dataclasses import replace
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import secrets
import socket
import threading
import time
import webbrowser

from host.protocol import RESPONSE, RUNNING, UNKNOWN, Sample, frame, unframe
from host.reference import NAMES, evaluate, load_profile, profile_crc
from host.board_link import BoardLink
from leader import Leader, isolated_leader_interface
from capture import Collector

ROOT = Path(__file__).resolve().parent
SITE_CONFIG = (ROOT.parent / 'amd_hybrid_deploy_20261031'
               / 'piper_towel_replica' / 'configs' / 'cobot_site.json')
RIGHT_J2 = 8
SLIDER_MIN = 1750
SLIDER_MAX = 2650
SLIDER_STEP = 10
MEDIA = {
    "/", "/index.html", "/folding_clip.mp4", "/folding_frame.png",
    "/counterexample.png", "/grasp_before.jpg", "/grasp_three_views.webm",
    "/interaction.js", "/booth.css",
}


class Booth:
    def __init__(self, address, port, leader_interface=None, protocol="v3", data_dir=None, *, auto_leader_can=False):
        profile = self.profile = load_profile(ROOT / "config/guard_profile.json")
        self.data = json.loads((ROOT / "sample.json").read_text(encoding="utf-8"))
        self.crc = profile_crc(profile)
        self.base = Sample(
            self.crc, self.data["step"], RUNNING, self.data["elapsed_us"], UNKNOWN, UNKNOWN,
            *(tuple(self.data[key]) for key in (
                "target", "measured", "previous", "act", "correction", "previous_correction"
            ))
        )
        if profile["action_names"][RIGHT_J2] != "right_joint_2.pos":
            raise ValueError("Unexpected right J2 channel")
        self.upper = profile["upper"][RIGHT_J2]
        if not SLIDER_MIN * 1000 <= self.upper < SLIDER_MAX * 1000:
            raise ValueError("The source sample or guard profile no longer matches the demonstration")
        self.session = secrets.randbits(32)
        self.sequence = 0
        self.lock = threading.Lock()
        self.sock = None
        self.protocol = protocol
        self.board_link = BoardLink((address, port), profile, self.crc) if address and protocol == 'v3' else None
        if address is not None and protocol == 'v1':
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.settimeout(1)
            self.sock.connect((address, port))
        self.leader = None
        self.leader_error = None
        self.auto_leader_can = auto_leader_can and not leader_interface
        if leader_interface:
            self._connect_leader(leader_interface)
        self.collector = Collector(self.leader, data_dir or ROOT / 'collected_data')

    def _connect_leader(self, interface):
        self.leader_error = None
        try:
            self.leader = Leader(interface)
        except (AttributeError, OSError) as exc:
            self.leader_error = f'无法打开右主臂 CAN 接口 {interface}。'
            print(f'Leader {interface}: {exc}', flush=True)

    def leader_status(self):
        with self.collector.lock:
            if self.auto_leader_can and self.collector.status()['state'] != 'recording':
                # Open a fresh listener for each confirmation, including after USB reconnects.
                if self.leader is not None:
                    self.leader.close()
                    self.leader = self.collector.leader = None
                try:
                    interface = isolated_leader_interface(json.loads(SITE_CONFIG.read_text(encoding='utf-8')))
                except RuntimeError as exc:
                    return dict(ready=False, error=str(exc))
                except (OSError, KeyError, TypeError, ValueError, AttributeError):
                    return dict(ready=False, error='无法确认执行臂与主臂接口。请工作人员检查设备配置后重试。')
                self._connect_leader(interface)
                self.collector.leader = self.leader
                if self.leader is not None:
                    return self.leader.status(wait_seconds=0.5)
            return self.leader.status() if self.leader else dict(ready=False, error=self.leader_error)

    def sample_for_angle(self, millirad):
        if (type(millirad) is not int or not SLIDER_MIN <= millirad <= SLIDER_MAX
                or (millirad - SLIDER_MIN) % SLIDER_STEP != 0):
            raise ValueError("角度超出演示滑块范围或步长，请刷新页面后重试。")
        target = list(self.base.target)
        target[RIGHT_J2] = millirad * 1000
        return replace(self.base, target=tuple(target))

    def result(self, sample):
        ref = evaluate(sample, self.profile)
        seq = self.sequence
        extra = {}
        if self.board_link is not None:
            reply, diagnostic = self.board_link.check(sample)
            seq = reply.sequence
            crc, mask, events, phase, *_ = reply.values
            ref = evaluate(diagnostic, self.profile)
            match = (mask, events, phase) == (ref['violations'], ref['events'], ref['phase'])
            extra = dict(session=reply.session, build_id=reply.values[8],
                         diagnostic_step=diagnostic.step)
        elif self.sock is None:
            self.sequence += 1
            mask = ref["violations"]
            match = None
        else:
            tick = time.monotonic_ns() // 1000
            packet = frame(1, self.session, seq, tick, sample.pack())
            self.sock.send(packet)
            try:
                raw = self.sock.recv(512)
            except (TimeoutError, ConnectionError) as exc:
                self.session = secrets.randbits(32)
                self.sequence = 0
                raise TimeoutError("板卡未回包。请检查连接和板端服务，等上一会话空闲 5 秒后重试。") from exc
            kind, session, sequence, returned_tick, payload = unframe(raw)
            if (kind, session, sequence, returned_tick) != (2, self.session, seq, tick):
                raise ValueError("回包标识与本次请求不匹配，请重试。")
            self.sequence += 1
            crc, mask, events, phase, _ = RESPONSE.unpack(payload)
            if crc != self.crc:
                raise ValueError("板端配置 CRC 不匹配，请核对实际加载的 profile。")
            match = (mask, events, phase) == (ref["violations"], ref["events"], ref["phase"])
        return dict(
            source="board" if self.sock is not None or self.board_link else "reference",
            seq=seq, frame=self.data["video_frame"], step=sample.step,
            value=sample.target[RIGHT_J2] / 1e6,
            mask=mask, names=[n for i, n in enumerate(NAMES) if mask & (1 << i)],
            reference_match=match, **extra
        )


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, booth, **kwargs):
        self.booth = booth
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_json(self, status, value):
        content = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.path == "/mode":
            self.send_json(HTTPStatus.OK, dict(
                source="board" if self.booth.sock is not None or self.booth.board_link else "reference",
                protocol=self.booth.protocol,
                expected_build_id=__import__('host.fold_protocol',fromlist=['BUILD_ID']).BUILD_ID,
                slider_min=SLIDER_MIN, slider_max=SLIDER_MAX, slider_step=SLIDER_STEP,
                upper_millirad=self.booth.upper / 1000,
                original_rad=self.booth.base.target[RIGHT_J2] / 1e6,
                leader_enabled=self.booth.leader is not None or self.booth.auto_leader_can,
                leader_error=self.booth.leader_error,
                data_dir=str(self.booth.collector.directory),
            ))
        elif self.path == "/leader/status":
            self.send_json(HTTPStatus.OK, self.booth.leader_status())
        elif self.path == '/capture/status':
            self.send_json(HTTPStatus.OK, self.booth.collector.status())
        elif self.path == '/capture/episodes':
            self.send_json(HTTPStatus.OK, dict(directory=str(self.booth.collector.directory),
                                              episodes=self.booth.collector.episodes()))
        elif self.path in MEDIA:
            if self.path == "/":
                self.path = "/index.html"
            try:
                super().do_GET()
            except ConnectionError:
                pass
        elif self.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if self.path not in ("/check/original", "/check/angle", "/capture/start", "/capture/stop", "/capture/frame"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        action = "record" if self.path.startswith('/capture/') else "check"
        if self.headers.get("X-FlexiFold-Action") != action:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if action == "record":
            self.capture_action()
            return
        sample = self.booth.base
        if self.path == "/check/angle":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 128:
                    raise ValueError("角度请求格式有误。")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict) or data.keys() != {"millirad"}:
                    raise ValueError("角度请求格式有误。")
                sample = self.booth.sample_for_angle(data["millirad"])
            except (ValueError, UnicodeError) as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
        try:
            with self.booth.lock:
                result = self.booth.result(sample)
        except (OSError, ValueError) as exc:
            self.send_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
        else:
            self.send_json(HTTPStatus.OK, result)

    def capture_action(self):
        try:
            limit = 450000 if self.path == '/capture/frame' else 4096
            length = int(self.headers.get('Content-Length', '0'))
            if not 1 <= length <= limit:
                raise ValueError('采集请求大小无效。')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict): raise ValueError('采集请求格式错误。')
            collector = self.booth.collector
            if self.path == '/capture/start':
                if set(data) != {'instruction', 'source'}: raise ValueError('开始请求字段有误。')
                result = collector.start(data['instruction'], data['source'])
            elif self.path == '/capture/stop':
                if set(data) != {'episode_id'}: raise ValueError('结束请求字段有误。')
                result = collector.stop(data['episode_id'])
            else:
                if set(data) != {'episode_id', 'jpeg_base64', 'client_elapsed_ms'}: raise ValueError('图像请求字段有误。')
                result = collector.add_frame(data['episode_id'], data['jpeg_base64'], data['client_elapsed_ms'])
        except RuntimeError as exc:
            self.send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
        except (ValueError, UnicodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except OSError as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": '保存失败：' + str(exc)})
        else:
            self.send_json(HTTPStatus.OK, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fpga", type=ipaddress.IPv4Address, help="IPv4 address of the running FPGA service")
    parser.add_argument("--udp-port", type=int, default=9009)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--protocol", choices=("v3", "v1"), default="v3",
                        help="B35 v3 by default; v1 must be selected for the legacy runtime")
    parser.add_argument("--leader-can", metavar="IFACE", help="listen for Piper leader commands on an isolated SocketCAN interface")
    parser.add_argument('--auto-leader-can', action='store_true', help='detect an isolated leader interface on request')
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'collected_data', help='dedicated raw episode directory')
    args = parser.parse_args()
    booth = Booth(str(args.fpga) if args.fpga else None, args.udp_port, args.leader_can, args.protocol, args.data_dir,
                  auto_leader_can=args.auto_leader_can)
    try:
        with ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, booth=booth)) as server:
            url = f"http://127.0.0.1:{server.server_port}/"
            print(url, "· FPGA UDP" if args.fpga else "· 软件参考预演", flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    finally:
        booth.collector.close()
        if booth.leader is not None:
            booth.leader.close()
        if booth.sock is not None:
            booth.sock.close()
        if booth.board_link is not None:
            booth.board_link.close()


if __name__ == "__main__":
    main()

