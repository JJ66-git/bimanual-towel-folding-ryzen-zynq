#!/usr/bin/env bash
# NPU visual encoder + ROCm hybrid and FPG1 diagnostic shadow.
set -eo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo '用法：AIPC_FPGA_SHADOW=FPGA_IPV4:9009 bash npu_deploy/run_hybrid_npu_fpga.sh [hybrid 参数]'
  echo '默认只读；加 --execute 才进入实际控制。此 FPG1 旁路不控制机械臂，也不喂 FWD1 看门狗。'
  echo '运行前在当前终端设置：sudo prlimit --pid $$ --memlock=1073741824:1073741824'
  exit 0
fi
: "${AIPC_FPGA_SHADOW:?请设置 AIPC_FPGA_SHADOW=FPGA_IPV4:9009}"
LIMIT="$(ulimit -l)"
if [[ "$LIMIT" != unlimited && "$LIMIT" -lt 1048576 ]]; then
  echo '请在同一终端先运行：sudo prlimit --pid $$ --memlock=1073741824:1073741824' >&2
  exit 1
fi
MEMORY_MAX="${AIPC_HYBRID_MEMORY_MAX:-16G}"
echo "NPU 视觉 + ROCm hybrid + FPGA FPG1 旁路；scope 内存上限 $MEMORY_MAX，CPU 配额 4 核，swap 为 0。"
echo '设备共享内存未必全部计入；检查 FPGA_SHADOW_FIRST_REPLY 和 FPGA_SHADOW_SUMMARY，FPG1 不喂 watchdog。'
finish_fpga() {
  local status=$?
  trap - EXIT
  if [[ "${AIPC_FPGA_AUTO_OFF:-0}" == 1 ]]; then
    local host="${AIPC_FPGA_SHADOW%:*}"
    if ! python3 "$ROOT/npu_deploy/fpga_lifecycle/control_client.py" finish --host "$host"; then
      echo 'FPGA 自动关闭请求失败；屏幕可能仍亮，请检查 FPGA 服务。' >&2
    fi
  fi
  exit "$status"
}
trap finish_fpga EXIT
systemd-run --user --scope --collect --quiet \
  "--property=MemoryMax=$MEMORY_MAX" --property=MemorySwapMax=0 --property=CPUQuota=400% \
  env AIPC_FPGA_SHADOW="$AIPC_FPGA_SHADOW" AIPC_FPGA_REQUIRE_START="${AIPC_FPGA_REQUIRE_START:-0}" \
  bash "$ROOT/npu_deploy/run_hybrid_npu.sh" "$@"
