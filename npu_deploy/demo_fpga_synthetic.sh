#!/usr/bin/env bash
# Guided FPGA protocol demonstration. Sends synthetic data; never controls the robot.
set -eo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo '用法：bash npu_deploy/demo_fpga_synthetic.sh FPGA_IPV4 [UDP端口]'
  echo '板端须先启动本工程统一服务并允许本机地址；发送合成任务和动作观测，不操作机械臂。'
  echo '成功后客户端发送 END；统一板端服务会结束会话并尝试恢复基线 overlay，再演示需重启板端服务。'
  exit 0
fi
if (( $# < 1 || $# > 2 )); then echo '请提供 FPGA IPv4 和可选 UDP 端口；使用 --help 查看说明。' >&2; exit 2; fi
IP="$1"
PORT="${2:-9009}"
if ! python3 - "$IP" "$PORT" <<'PY'
import ipaddress, sys
try:
    addr = ipaddress.ip_address(sys.argv[1])
    port = int(sys.argv[2])
    if addr.version != 4 or addr.is_multicast or addr.is_unspecified or not 1 <= port <= 65535:
        raise ValueError
except ValueError:
    sys.exit(1)
PY
then echo '请输入有效的单播 IPv4 和 1–65535 范围的 UDP 端口。' >&2; exit 2; fi
if [[ -f "$ROOT/fpga_integration/aipc_client/host/coop_demo.py" ]]; then
  CLIENT="$ROOT/fpga_integration/aipc_client"
elif [[ -f "$ROOT/fpga/host/coop_demo.py" ]]; then
  CLIENT="$ROOT/fpga"
else
  echo '找不到 FPGA 客户端源码：需要 fpga_integration/aipc_client 或 fpga。' >&2; exit 1
fi
mkdir -p "$ROOT/npu_deploy/artifacts/demo"
SESSION="$(mktemp -d "$ROOT/npu_deploy/artifacts/demo/fpga_$(date +%Y%m%d_%H%M%S)_XXXXXX")"
echo 'FPGA 合成协议演示：由主机发出样例状态和动作，不向机械臂发送命令。'
echo '板端需要已启动匹配的统一服务；END 后该服务会结束会话并尝试恢复基线 overlay。'
echo "目标：$IP:$PORT；本次日志：$SESSION"
printf '$ ip route get %q\n' "$IP"
if ! ip route get "$IP"; then echo '无法查询路由，未发送演示数据。' >&2; exit 1; fi
printf '$ cd %q && python3 -B -m host.coop_demo --udp %q --out %q\n' "$CLIENT" "$IP:$PORT" "$SESSION/client"
set +e
( cd "$CLIENT" && env -u PYTHONPATH -u PYTHONHOME -u LD_PRELOAD -u LD_LIBRARY_PATH -u GLIBC_TUNABLES \
    python3 -B -u -m host.coop_demo --udp "$IP:$PORT" --out "$SESSION/client" ) 2>&1 | tee "$SESSION/console.log"
status=${PIPESTATUS[0]}
set -e
if (( status != 0 )); then
  echo "FPGA 合成演示失败（退出状态 $status）。检查板端服务、peer 地址、端口及本次日志；未完成的会话可能需要在板端重新启动服务。" >&2
  exit "$status"
fi
python3 "$ROOT/npu_deploy/demo_fpga_report.py" "$SESSION/client"
echo "完成。本次记录：$SESSION/client；板端日志需要从板卡另外查看。"
