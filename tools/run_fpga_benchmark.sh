#!/usr/bin/env bash
# No cameras, CAN initialization, reset or policy execution.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="$ROOT"
exec "$PROJECT/amd_hybrid_deploy_20261031/.venv/bin/python" \
  "$PROJECT/npu_deploy/fpga_authority/benchmark.py" --prepare-board --samples 750 \
  --out "$ROOT/../local_environment_record/fpga_authority_phase1_20261114/board_benchmark.json" "$@"
