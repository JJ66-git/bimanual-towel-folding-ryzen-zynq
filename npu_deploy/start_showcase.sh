#!/usr/bin/env bash
# AMD platform proof -> FPGA service -> reset -> 750-step hybrid execution.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="$ROOT/amd_hybrid_deploy_20261031"
FPGA_ADDRESS="${AIPC_FPGA_SHADOW:-192.168.2.99:9009}"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo '正式脚本：展示 AMD 平台、确认 FPGA 服务、复位、启动 750 步 NPU/ROCm hybrid、实时界面与 FPGA 旁路。'
  echo '用法：bash npu_deploy/start_showcase.sh [--check-only]'
  echo '正式运行分阶段按 Enter 确认；复位与任务会实际移动机械臂。输入 q 或按 Ctrl+C 退出。'
  echo '先运行准备脚本启动 FPGA；FPGA 地址可用 AIPC_FPGA_SHADOW=IPv4:端口 指定。'
  exit 0
fi
if [[ "${1:-}" == --check-only && $# == 1 ]]; then
  echo "正式预览：Enter → AMD-SMI/ROCm/XRT → Enter → FPGA FUI1 $FPGA_ADDRESS → Enter → 实际复位 → Enter → 750 步 hybrid + 实时界面。"
  echo '不会访问硬件或修改配置。'; exit 0
fi
if (( $# )); then echo '未知参数；使用 --help。' >&2; exit 2; fi
trap 'echo "已收到停止信号，脚本不再启动后续阶段。" >&2; exit 130' INT TERM

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

echo '现场正式流程：AMD 展示 → FPGA 服务确认 → 复位 → 750 步任务。'
wait_for_enter '准备展示 AMD 平台。'
LIMIT="$(ulimit -l)"
if [[ "$LIMIT" != unlimited && "$LIMIT" -lt 1048576 ]]; then
  echo '调整本脚本及子进程的 memlock 上限为 1 GiB（不是预分配）；系统可能要求 sudo 密码。'
  sudo prlimit --pid $$ --memlock=1073741824:1073741824
fi
mkdir -p "$ROOT/npu_deploy/artifacts/showcase"
SESSION="$(mktemp -d "$ROOT/npu_deploy/artifacts/showcase/work_$(date +%Y%m%d_%H%M%S)_XXXXXX")"
echo "本次日志目录：$SESSION"
echo '[1/4] 显示真实 AMD 平台和 ROCm 计算'
python3 "$ROOT/npu_deploy/field_budget.py"
echo '$ lscpu | grep "Model name"'
lscpu | grep 'Model name'
echo '$ amd-smi'
env -u LD_PRELOAD -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME -u GLIBC_TUNABLES "$PROJECT/.venv/bin/amd-smi"
echo '$ ROCm 矩阵计算及 CPU 对照'
env -u LD_PRELOAD -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME -u GLIBC_TUNABLES \
  OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
  "$PROJECT/.venv/bin/python" "$ROOT/npu_deploy/demo_rocm_compute.py"
echo '$ xrt-smi examine'
( set +u
  unset LD_PRELOAD LD_LIBRARY_PATH PYTHONPATH PYTHONHOME GLIBC_TUNABLES
  source /opt/xilinx/xrt/setup.sh
  xrt-smi examine ) 2>&1 | tee "$SESSION/xrt_examine.log"
if ! grep -Eq '^\|.*NPU.*\|' "$SESSION/xrt_examine.log"; then echo 'XRT 未显示 NPU。' >&2; exit 1; fi
wait_for_enter 'AMD 平台展示完成，准备确认 FPGA 服务。'
echo '[2/4] 确认 FPGA 服务响应（FUI1）'
printf '$ python3 %q %q\n' "$ROOT/npu_deploy/fpga_service_probe.py" "$FPGA_ADDRESS"
if ! python3 "$ROOT/npu_deploy/fpga_service_probe.py" "$FPGA_ADDRESS"; then
  echo 'FPGA 服务未返回匹配确认；请在板端重新启动 host_observation 服务后重试。' >&2; exit 1
fi
echo 'FPGA 服务已响应；运行中的 FPG1 回包进一步显示真实 PL 检查结果。'
wait_for_enter 'FPGA 服务已响应。确认后将进入实际复位，机械臂会移动。'
echo '[3/4] 实际复位：会使能并移动两臂'
echo '已确认复位；倒计时期间按 Ctrl+C 可取消。任务开始前还会再次等待 Enter。'
for n in 5 4 3 2 1; do echo "自动复位倒计时：$n"; sleep 1; done
env AIPC_RESET_AUTO_START=1 "$PROJECT/run.sh" reset --execute --trace 2>&1 | tee "$SESSION/reset.log"
if ! grep -q '^RESET_EXECUTION_COMPLETE$' "$SESSION/reset.log"; then
  echo '没有收到复位完成标记，不启动后续任务。' >&2; exit 1
fi
wait_for_enter '复位完成。确认后将正式运行最多 750 步，机械臂会移动。'
echo '[4/4] 正式执行 750 步 hybrid：NPU 视觉 + ROCm + FPGA 旁路 + 实时界面'
echo 'Ctrl+C 请求停止；控制器会尝试保持测得的当前位置，不会自动失使能。'
echo '任务启动前使用同一 FPG1 会话验证 PL；运行中 FPGA 是非阻塞旁路，不对动作或 watchdog 提供门控。'
export AIPC_FPGA_SHADOW="$FPGA_ADDRESS"
export AIPC_FPGA_REQUIRE_START=1
export AIPC_FPGA_AUTO_OFF=1
exec bash "$ROOT/npu_deploy/run_hybrid_npu_fpga.sh" --steps 750 --execute --trace --preview
