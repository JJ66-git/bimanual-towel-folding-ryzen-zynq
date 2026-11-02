#!/usr/bin/env bash
# Bounded, observation-only dashboard test; no policy or FPGA required.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" == --check-only ]]; then
  exec "$ROOT/amd_hybrid_deploy_20261031/.venv/bin/python" "$ROOT/npu_deploy/check_dashboard_live.py" --check-only
fi
if (( $# )); then echo '用法：bash npu_deploy/run_dashboard_readonly.sh [--check-only]' >&2; exit 2; fi
printf '%s\n' '20 秒观察检查：会打开三路相机和两臂 CAN；SDK 会发送查询，不发送使能、复位或运动目标。'
printf '%s\n' '请先结束任何占用相机或机械臂的任务；关闭窗口或按 Ctrl+C 可提前结束。'
exec systemd-run --user --scope --collect --quiet \
  --property=MemoryMax=4G --property=MemorySwapMax=0 --property=CPUQuota=200% \
  env -u LD_PRELOAD -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME -u GLIBC_TUNABLES \
  OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
  "$ROOT/amd_hybrid_deploy_20261031/.venv/bin/python" "$ROOT/npu_deploy/check_dashboard_live.py"
