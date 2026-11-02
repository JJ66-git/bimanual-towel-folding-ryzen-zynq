#!/usr/bin/env bash
# Explicit 10-step ACT motion with observational FPG1 shadow; no NPU vision.
set -eo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo '用法：AIPC_FPGA_SHADOW=FPGA_IPV4:9009 bash npu_deploy/run_act_fpga_motion_10.sh --check-only|--execute'
  echo '--check-only 只打印底层命令；--execute 会使能并让两只机械臂执行最多 10 步 ACT 动作。'
  echo '本入口不使用 NPU 视觉编码；FPGA 为诊断旁路，回包和超时不控制运动。'
  exit 0
fi
if (( $# != 1 )) || [[ "$1" != --execute && "$1" != --check-only ]]; then
  echo '请明确指定 --check-only 或 --execute；使用 --help 查看作用。' >&2; exit 2
fi
if [[ "$1" == --check-only ]]; then
  exec "$ROOT/amd_hybrid_deploy_20261031/run.sh" act --backend rocm --steps 10 --trace --execute --check-only
fi
: "${AIPC_FPGA_SHADOW:?请设置 AIPC_FPGA_SHADOW=FPGA_IPV4:9009}"
MEMORY_MAX="${AIPC_HYBRID_MEMORY_MAX:-16G}"
echo '即将进入真实运动：最多 10 步 ACT、ROCm 和 FPGA FPG1 旁路；不使用 NPU 视觉编码。'
echo 'FPGA 为非阻塞诊断旁路，丢包不会阻止机械臂动作。确认板端为新会话并观察日志。'
echo "scope 内存上限 $MEMORY_MAX，CPU 4 核，swap 0；设备共享内存未必全部计入。"
exec systemd-run --user --scope --collect --quiet \
  "--property=MemoryMax=$MEMORY_MAX" --property=MemorySwapMax=0 --property=CPUQuota=400% \
  env -u LD_PRELOAD -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME -u GLIBC_TUNABLES \
  "AIPC_FPGA_SHADOW=$AIPC_FPGA_SHADOW" \
  "$ROOT/amd_hybrid_deploy_20261031/run.sh" act --backend rocm --steps 10 --trace --execute
