#!/usr/bin/env python3
"""Copy the session fix over an already logged-in PYNQ USB serial shell."""
import argparse
import base64
import hashlib
from pathlib import Path
import re
import secrets
import sys
import time


def remote_digest(lines):
    """Extract sha256sum output even when a shell prompt precedes it."""
    pattern = re.compile(r'(?:^|\s)([0-9a-fA-F]{64})\s+\*?/tmp/fpga_session_fix\.tar\.gz(?:\s|$)')
    for line in lines:
        match = pattern.search(line)
        if match:
            return match.group(1).lower()
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='/dev/ttyUSB0', help='USB serial port')
    parser.add_argument('--baud', type=int, default=115200)
    args = parser.parse_args()
    try:
        import serial
    except ImportError:
        sys.exit('缺少 pyserial；请先确认 AI PC 的 Python 环境中已安装 pyserial。')
    package = Path(__file__).resolve().parent / 'fpga_session_fix.tar.gz'
    data = package.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    encoded = base64.b64encode(data).decode('ascii')
    suffix = secrets.token_hex(8).upper()
    begin, end, ready, done, delimiter = (f'CODEX_{name}_{suffix}' for name in ('BEGIN','END','READY','DONE','EOF'))
    print(f'目标串口：{args.port}，波特率：{args.baud}。')
    print('仅在该串口已登录 FPGA 的 root@pynq、板端服务已停止、其他串口程序已关闭时继续。')
    if input('按 Enter 继续，输入 q 退出：').strip().lower() == 'q':
        return
    try:
        port = serial.Serial(port=None, baudrate=args.baud, timeout=0.2, write_timeout=3)
        port.dtr = False
        port.rts = False
        port.port = args.port
        port.open()
    except (serial.SerialException, OSError) as exc:
        sys.exit(f'无法打开串口：{exc}。请检查端口、权限及是否被其他终端占用。')
    state = {'echo_disabled': False, 'in_heredoc': False}
    try:
        def send(text):
            port.write(text.encode('ascii'))
            port.flush()
        def until(marker, timeout):
            deadline = time.monotonic() + timeout
            buffer = bytearray()
            while time.monotonic() < deadline:
                buffer.extend(port.read(1024))
                lines = bytes(buffer).decode('utf-8', errors='replace').replace('\r', '').split('\n')
                if marker in lines:
                    return lines
                if len(buffer) > 65536:
                    raise RuntimeError('串口输出过多，终止传输。')
            raise TimeoutError('串口未返回预期标记，请检查是否已登录 FPGA shell 及波特率。')
        send("\n" + f"printf '\\n%s\\n' '{begin}'; hostname; id -un; printf '%s\\n' '{end}'\n")
        lines = until(end, 8)
        try:
            identity = lines[lines.index(begin)+1:lines.index(end)]
        except ValueError as exc:
            raise RuntimeError('无法确认串口身份；没有上传文件。') from exc
        if 'pynq' not in identity or 'root' not in identity:
            raise RuntimeError(f'串口身份不是预期的 root@pynq：{identity!r}；没有上传文件。')
        print('已通过串口确认主机 pynq 和 root 用户。', flush=True)
        send(f"codex_saved_tty=$(stty -g); stty -echo; printf '\\n%s\\n' '{ready}'\n")
        state['echo_disabled'] = True
        until(ready, 8)
        send(f"base64 -d > /tmp/fpga_session_fix.tar.gz <<'{delimiter}'\n")
        state['in_heredoc'] = True
        for start in range(0, len(encoded), 64):
            send(encoded[start:start+64] + '\n')
            time.sleep(0.02)
        send(delimiter + '\n')
        state['in_heredoc'] = False
        send(f"sha256sum /tmp/fpga_session_fix.tar.gz; stty \"$codex_saved_tty\"; printf '\\n%s\\n' '{done}'\n")
        result = until(done, 15)
        state['echo_disabled'] = False
        actual = remote_digest(result)
        if actual != digest:
            raise RuntimeError(f'板端文件 SHA-256 不匹配或无法读取（本地={digest}，板端={actual or "未识别"}）；请不要安装。')
        print('SERIAL_TRANSFER_OK SHA256=' + digest)
        print('文件已保存至 FPGA：/tmp/fpga_session_fix.tar.gz；尚未安装或启动服务。')
    except (RuntimeError, TimeoutError, serial.SerialException, OSError) as exc:
        print(f'串口传输失败：{exc}', file=sys.stderr)
        if state['echo_disabled']:
            try:
                if state['in_heredoc']:
                    send('\x03')
                send('stty "$codex_saved_tty"\n')
            except Exception:
                pass
        sys.exit(1)
    finally:
        port.close()


if __name__ == '__main__':
    main()
