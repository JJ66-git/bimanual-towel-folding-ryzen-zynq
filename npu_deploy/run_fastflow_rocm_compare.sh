#!/usr/bin/env bash
# Same fixture and fixed noise; both phases are offline and use no robot.
set -eo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="$ROOT/npu_deploy/artifacts/fastflow_custom"
LIMIT="$(ulimit -l)"
if [[ "$LIMIT" != unlimited && "$LIMIT" -lt 1048576 ]]; then
  echo "锁定内存上限为 $LIMIT KiB，请先在当前终端运行：sudo prlimit --pid \$\$ --memlock=unlimited:unlimited" >&2
  exit 1
fi
echo '生成相同输入和噪声的 ROCm 原生模型参考……'
set +e
env -u LD_PRELOAD -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME -u GLIBC_TUNABLES \
  HF_HOME="${HF_HOME:-/tmp/fpga-hf-cache}" \
  "$ROOT/amd_hybrid_deploy_20261031/.venv/bin/python" "$ROOT/npu_deploy/prepare_fastflow_reference.py" \
  --backend rocm --warmup 1 --repeat 3 > "$DIR/reference_rocm.log" 2>&1
RESULT=$?
set -e
grep -E 'REFERENCE|Error|ERROR|Traceback|RuntimeError' "$DIR/reference_rocm.log" | tail -15 || true
if [[ "$RESULT" != 0 ]]; then
  echo "ROCm 参考失败，日志：$DIR/reference_rocm.log" >&2
  exit "$RESULT"
fi
echo '执行完整 SmolVLA FastFlowLM NPU 前向对照……'
exec env FASTFLOW_LOG_SUFFIX=_rocm bash "$ROOT/npu_deploy/run_fastflow_check.sh" --reference-file "$DIR/fixture_reference_rocm.npz"
