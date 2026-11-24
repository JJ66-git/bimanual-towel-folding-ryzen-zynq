#!/usr/bin/env bash
# Read-only preparation report; never enables or moves the robot.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/npu_deploy/runtime_resources.sh"
exec env -u LD_PRELOAD -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME -u GLIBC_TUNABLES \
 OMP_NUM_THREADS="$OMP_NUM_THREADS" MKL_NUM_THREADS="$MKL_NUM_THREADS" OPENBLAS_NUM_THREADS="$OPENBLAS_NUM_THREADS" \
 "$ROOT/amd_hybrid_deploy_20261031/.venv/bin/python" "$ROOT/npu_deploy/check_before_hardware.py" "$@"
