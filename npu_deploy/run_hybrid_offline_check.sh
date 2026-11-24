#!/usr/bin/env bash
# Run one offline ACT + NPU-vision SmolVLA blend replay in a limited user scope.
set -eo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/npu_deploy/runtime_resources.sh"
LIMIT="$(ulimit -l)"
if [[ "$LIMIT" != unlimited && "$LIMIT" -lt 1048576 ]]; then
  echo "当前终端锁定内存上限为 $LIMIT KiB，请先执行：sudo prlimit --pid \$\$ --memlock=1073741824:1073741824" >&2
  exit 1
fi
LOG="$ROOT/npu_deploy/artifacts/smol_vision/hybrid_offline.log"
echo "离线回放：scope 内存上限 $AIPC_RUNTIME_MEMORY_MAX；CPU 配额 $AIPC_RUNTIME_CPU_QUOTA（全部 $AIPC_RUNTIME_CPUS 个逻辑核可用）；swap 0。"
echo '加载模型和 NPU 运行时可能需要几分钟，详细日志写入文件。'
set +e
systemd-run --user --scope --collect --quiet \
  "--property=MemoryMax=$AIPC_RUNTIME_MEMORY_MAX" --property=MemorySwapMax=0 "--property=CPUQuota=$AIPC_RUNTIME_CPU_QUOTA" \
  env -u LD_PRELOAD -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME -u GLIBC_TUNABLES \
  HF_HOME="${HF_HOME:-/tmp/fpga-hf-cache}" OMP_NUM_THREADS="$OMP_NUM_THREADS" MKL_NUM_THREADS="$MKL_NUM_THREADS" OPENBLAS_NUM_THREADS="$OPENBLAS_NUM_THREADS" \
  "$ROOT/amd_hybrid_deploy_20261031/.venv/bin/python" "$ROOT/npu_deploy/check_hybrid_offline.py" "$@" >"$LOG" 2>&1
RESULT=$?
set -e
echo "退出状态：$RESULT"
grep -E 'ACT_READY|SMOL_VISION_BACKEND|Actually running on NPU|HYBRID_OFFLINE|NATIVE_REPLAY|NPU_REPLAY|HYBRID_REPORT|Traceback|ERROR|Error|RuntimeError' "$LOG" | tail -35 || true
echo "完整日志：$LOG"
exit "$RESULT"
