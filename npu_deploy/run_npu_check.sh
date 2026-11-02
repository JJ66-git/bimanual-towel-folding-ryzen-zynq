#!/usr/bin/env bash
# Offline-only hardware test; caller's shell should have >=1 GiB memlock.
set -eo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/npu_deploy/artifacts/smol_vision/npu.log"
LIMIT="$(ulimit -l)"
if [[ "$LIMIT" != unlimited && "$LIMIT" -lt 1048576 ]]; then
  echo "锁定内存上限为 $LIMIT KiB，请在当前终端先运行：sudo prlimit --pid \$\$ --memlock=1073741824:1073741824" >&2
  exit 1
fi
source /opt/xilinx/xrt/setup.sh
source "$ROOT/ryzen-ai-npu/venv/bin/activate"
PYTHON_LIB="$ROOT/amd_hybrid_deploy_20261031/.tooling/python/cpython-3.12.16-linux-x86_64-gnu/lib"
export LD_LIBRARY_PATH="/opt/xilinx/xrt/lib:$PYTHON_LIB:$LD_LIBRARY_PATH"
export LD_PRELOAD=/opt/xilinx/xrt/lib/libxrt_driver_xdna.so.2
export GLIBC_TUNABLES=glibc.rtld.execstack=2
set +e
python "$ROOT/npu_deploy/check_smol_vision.py" --provider npu --warmup 1 --repeat 3 > "$LOG" 2>&1
RESULT=$?
set -e
echo "退出状态：$RESULT"
grep -E 'NPU|IMAGE_RESULT|CHECK_REPORT|ERROR|Error|Traceback|Failed' "$LOG" | tail -30 || true
echo "完整日志：$LOG"
exit "$RESULT"
