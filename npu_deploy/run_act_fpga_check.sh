#!/usr/bin/env bash
# Short read-only ACT + FPGA FPG1 shadow check; no NPU compilation.
set -eo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo '用法：AIPC_FPGA_SHADOW=FPGA_IPV4:9009 bash npu_deploy/run_act_fpga_check.sh'
  echo '只读执行 10 步 ACT + FPGA 旁路，打开真实相机并读取机械臂反馈，不使能或发送动作目标。'
  echo '此脚本不使用 NPU 视觉编码；ACT 原入口仍会加载 SmolVLA 模型。'
  exit 0
fi
if (( $# )); then echo '该检查不接受额外参数。' >&2; exit 2; fi
: "${AIPC_FPGA_SHADOW:?请设置 AIPC_FPGA_SHADOW=FPGA_IPV4:9009}"
MEMORY_MAX="${AIPC_HYBRID_MEMORY_MAX:-16G}"
echo "只读检查：真实相机、两臂反馈、ROCm ACT 和 FPGA 旁路；内存上限 $MEMORY_MAX，CPU 4 核，swap 0。"
echo '设备共享内存未必全部计入；该路径不使用 NPU 视觉编码。'
echo '板端 FPG1 服务每次只接受一个会话；若前次使用过，请先在 FPGA 终端停止并重启板端服务。'
exec systemd-run --user --scope --collect --quiet \
  "--property=MemoryMax=$MEMORY_MAX" --property=MemorySwapMax=0 --property=CPUQuota=400% \
  env -u LD_PRELOAD -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME -u GLIBC_TUNABLES \
  "AIPC_FPGA_SHADOW=$AIPC_FPGA_SHADOW" \
  "$ROOT/amd_hybrid_deploy_20261031/run.sh" act --backend rocm --steps 10 --trace
