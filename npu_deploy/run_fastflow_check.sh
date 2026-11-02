#!/usr/bin/env bash
# Offline full SmolVLA plain inference on NPU; no robot or camera access.
set -eo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL="$ROOT/npu_deploy/artifacts/fastflow_custom_bf16"
LOG="$MODEL/fastflow${FASTFLOW_LOG_SUFFIX:-}.log"
LIMIT="$(ulimit -l)"
if [[ "$LIMIT" != unlimited && "$LIMIT" -lt 1048576 ]]; then
  echo "锁定内存上限为 $LIMIT KiB，请在当前终端先运行：sudo prlimit --pid \$\$ --memlock=1073741824:1073741824" >&2
  exit 1
fi
PYLIB="$ROOT/amd_hybrid_deploy_20261031/.tooling/python/cpython-3.12.16-linux-x86_64-gnu/lib"
set +e
env -u PYTHONPATH -u PYTHONHOME -u GLIBC_TUNABLES \
  LD_LIBRARY_PATH="/opt/xilinx/xrt/lib:$PYLIB" \
  LD_PRELOAD=/opt/xilinx/xrt/lib/libxrt_driver_xdna.so.2 \
  HF_HOME="${HF_HOME:-/tmp/fpga-hf-cache}" \
  "$ROOT/amd_hybrid_deploy_20261031/.venv/bin/python" "$ROOT/npu_deploy/check_fastflow_smol.py" --model-dir "$MODEL" "$@" > "$LOG" 2>&1
RESULT=$?
set -e
echo "退出状态：$RESULT"
grep -Ei 'FASTFLOW_|ERROR|Traceback|Failed|Exception|insufficient' "$LOG" | tail -30 || true
echo "完整日志：$LOG"
exit "$RESULT"
