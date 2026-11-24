#!/usr/bin/env bash
# Real AMD hardware/software evidence only; no camera, CAN or robot access.
set -eo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/npu_deploy/runtime_resources.sh"
PROJECT="$ROOT/amd_hybrid_deploy_20261031"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then echo '用法：bash npu_deploy/check_amd_platform.sh [--check-only]';exit 0;fi
if [[ "${1:-}" == --check-only ]]; then echo 'AMD CPU + ROCm matrix/CPU comparison + official NPU quicktest; no robot access';exit 0;fi
if (( $# )); then echo '未知参数。' >&2;exit 2;fi
panel_event() { if [[ -n "${AIPC_SHOWCASE_PANEL_TOKEN:-}" ]]; then printf '\nFLEXIFOLD_PANEL:%s:%s:%s\n' "$AIPC_SHOWCASE_PANEL_TOKEN" "$1" "$2";fi; }
panel_event GATE cpu
read -r reply
if [[ -n "$reply" ]]; then exit 130;fi
panel_event STAGE cpu
LC_ALL=C lscpu | grep 'Model name'
panel_event STAGE rocm
env -u LD_PRELOAD -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME -u GLIBC_TUNABLES \
  "$PROJECT/.venv/bin/python" "$ROOT/npu_deploy/demo_rocm_compute.py"
panel_event STAGE npu
mkdir -p "$ROOT/npu_deploy/artifacts/amd_check"
SESSION="$(mktemp -d "$ROOT/npu_deploy/artifacts/amd_check/run_XXXXXXXX")"
(
 source /opt/xilinx/xrt/setup.sh
 xrt-smi examine
 systemd-run --user --scope --collect --quiet "--property=MemoryMax=$AIPC_RUNTIME_MEMORY_MAX" --property=MemorySwapMax=0 "--property=CPUQuota=$AIPC_RUNTIME_CPU_QUOTA" \
  env -u LD_PRELOAD -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH OMP_NUM_THREADS="$OMP_NUM_THREADS" MKL_NUM_THREADS="$MKL_NUM_THREADS" OPENBLAS_NUM_THREADS="$OPENBLAS_NUM_THREADS" \
  bash -c 'source /opt/xilinx/xrt/setup.sh; source "$1/bin/activate"; export LD_LIBRARY_PATH="/lib/x86_64-linux-gnu:$RYZEN_AI_INSTALLATION_PATH/onnxruntime/lib/:${LD_LIBRARY_PATH:-}"; cd "$1/quicktest"; exec python -u quicktest.py' \
  _ "$ROOT/ryzen-ai-npu/venv"
) 2>&1 | tee "$SESSION/npu.log"
grep -Eq 'Actually running on NPU[[:space:]]+[1-9][0-9]*' "$SESSION/npu.log"
grep -q '^Test Finished$' "$SESSION/npu.log"
echo 'NPU_QUICKTEST_PASSED: official model ran with an NPU subgraph'
echo "AMD_CHECK_LOG=$SESSION"
