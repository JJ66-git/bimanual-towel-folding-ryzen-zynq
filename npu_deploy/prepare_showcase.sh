#!/usr/bin/env bash
# Prepare hardware without enabling the robot or issuing motion targets.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="$ROOT/amd_hybrid_deploy_20261031"
FPGA_ADDRESS="${AIPC_FPGA_SHADOW:-192.168.2.99:9009}"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo '准备脚本：设置 750 步、识别相机与 CAN、监听机械臂反馈、检查 FPGA 网络和 NPU。'
  echo '用法：bash npu_deploy/prepare_showcase.sh [--check-only]'
  echo '各阶段按 Enter 继续、q 退出；不使能或移动机械臂，可能请求 sudo。FPGA 地址可用 AIPC_FPGA_SHADOW=IPv4:端口 指定。'
  exit 0
fi
if [[ "${1:-}" == --check-only && $# == 1 ]]; then
  echo "准备预览：750 步；$PROJECT/run.sh prepare；FPGA 网络 $FPGA_ADDRESS；NPU 固定图片预检。"
  echo '不会访问硬件或修改配置。'; exit 0
fi
if (( $# )); then echo '未知参数；使用 --help。' >&2; exit 2; fi
trap 'echo "准备已中断，未继续执行后续步骤。" >&2; exit 130' INT TERM

wait_for_enter() {
  local reply
  while true; do
    printf '\n%s\n' "$1"
    printf '按 Enter 继续；输入 q 退出：'
    if ! IFS= read -r reply; then
      echo '输入结束，脚本退出。' >&2
      exit 130
    fi
    case "$reply" in
      '') return 0 ;;
      q|Q) echo '已退出，不再进入后续阶段。'; exit 130 ;;
      *) echo '请直接按 Enter，或输入 q 退出。' ;;
    esac
  done
}

echo '现场准备流程：预算与绑定 → 机械臂反馈 → FPGA 网络 → NPU 检查。'
wait_for_enter '准备开始设备检查。'
LIMIT="$(ulimit -l)"
if [[ "$LIMIT" != unlimited && "$LIMIT" -lt 1048576 ]]; then
  echo '调整本脚本及子进程的 memlock 上限为 1 GiB（不是预分配）；系统可能要求 sudo 密码。'
  sudo prlimit --pid $$ --memlock=1073741824:1073741824
fi
mkdir -p "$ROOT/npu_deploy/artifacts/showcase"
SESSION="$(mktemp -d "$ROOT/npu_deploy/artifacts/showcase/prepare_$(date +%Y%m%d_%H%M%S)_XXXXXX")"
echo "准备日志目录：$SESSION"
echo '[1/4] 设置现场 750 步，核对 FPGA 源码配置'
python3 "$ROOT/npu_deploy/field_budget.py" --apply
wait_for_enter '预算已核对，准备识别相机和两臂并监听反馈。'
echo '[2/4] 识别两臂 USB-CAN、相机并监听反馈'
"$PROJECT/run.sh" prepare 2>&1 | tee "$SESSION/prepare.log"
python3 "$ROOT/npu_deploy/check_preparation_result.py" "$SESSION/prepare.log"
wait_for_enter '相机身份与机械臂反馈检查完成，准备检查 FPGA 网络。'
echo '[3/4] 检查 FPGA 网络地址'
FPGA_IP="${FPGA_ADDRESS%:*}"
python3 - "$FPGA_ADDRESS" <<'PY'
import ipaddress,sys
host,sep,port=sys.argv[1].rpartition(':')
if not sep or not 1 <= int(port) <= 65535: raise SystemExit('FPGA 地址须为 IPv4:端口')
ipaddress.IPv4Address(host)
PY
ROUTE="$(ip route get "$FPGA_IP")"
printf '%s\n' "$ROUTE"
if ! ping -c 2 -W 2 "$FPGA_IP"; then
  echo 'FPGA 地址当前不可达；请核对网线、板卡 IP 及 AI PC 有线网卡地址。' >&2
  echo '可运行：ip -br -4 address 和 ip -4 route。NPU 预检尚未开始。' >&2
  exit 1
fi
SRC_IP="$(printf '%s\n' "$ROUTE" | awk '{for(i=1;i<=NF;i++) if($i=="src") {print $(i+1); exit}}')"
echo 'FPGA IP 可达；运行服务和 PL 将在正式脚本中继续检查。'
echo '启动 FPGA 显示和诊断服务，等待板端确认……'
python3 "$ROOT/npu_deploy/fpga_lifecycle/control_client.py" start --host "$FPGA_IP"
if [[ -n "$SRC_IP" ]]; then
  echo "AI PC 有线地址：$SRC_IP；请确认与 FPGA 生命周期服务的允许地址一致。"
fi
wait_for_enter 'FPGA 网络检查完成，准备运行 NPU 视觉预检。'
echo '[4/4] 独立 NPU 视觉预检（可能需要数分钟）'
bash "$ROOT/npu_deploy/run_npu_preflight.sh"
echo 'PREPARATION_COMPLETE: 750 步、机械臂反馈、相机身份、FPGA 网络和 NPU 预检已完成。'
echo 'FPGA 显示和诊断服务已启动；没有自动启动或使能机械臂。'
