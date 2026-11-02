#!/usr/bin/env bash
# Executed inside the demonstration's resource-limited user scope.
set -eo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO_DIR="$1"
source /opt/xilinx/xrt/setup.sh
source "$ROOT/ryzen-ai-npu/venv/bin/activate"
PYTHON_LIB="$ROOT/amd_hybrid_deploy_20261031/.tooling/python/cpython-3.12.16-linux-x86_64-gnu/lib"
export LD_LIBRARY_PATH="/opt/xilinx/xrt/lib:$PYTHON_LIB:${LD_LIBRARY_PATH:-}"
export LD_PRELOAD=/opt/xilinx/xrt/lib/libxrt_driver_xdna.so.2
export GLIBC_TUNABLES=glibc.rtld.execstack=2
exec python "$ROOT/npu_deploy/check_smol_vision.py" --artifact-dir "$DEMO_DIR" --provider npu --warmup 0 --repeat 1
