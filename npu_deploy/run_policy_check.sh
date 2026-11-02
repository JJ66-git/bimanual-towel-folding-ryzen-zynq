#!/usr/bin/env bash
# Compares native ROCm and experimental NPU vision without accessing a robot.
set -eo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/npu_deploy/artifacts/smol_vision/policy_rocm_npu.log"
LIMIT="$(ulimit -l)"
if [[ "$LIMIT" != unlimited && "$LIMIT" -lt 1048576 ]]; then
  echo "锁定内存上限为 $LIMIT KiB，请在当前终端先运行：sudo prlimit --pid \$\$ --memlock=1073741824:1073741824" >&2
  exit 1
fi
set +e
env -u LD_PRELOAD -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME -u GLIBC_TUNABLES \
  HF_HOME="${HF_HOME:-/tmp/fpga-hf-cache}" \
  OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
  "$ROOT/amd_hybrid_deploy_20261031/.venv/bin/python" "$ROOT/npu_deploy/compare_smol_policy.py" \
  --backend rocm --worker npu --warmup 0 --repeat 1 "$@" > "$LOG" 2>&1
RESULT=$?
set -e
echo "退出状态：$RESULT"
grep -E 'NPU|sample=|POLICY_REPORT|ERROR|Error|Traceback|Failed|RuntimeError' "$LOG" | tail -40 || true
echo "完整日志：$LOG"
exit "$RESULT"
