#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/../amd_hybrid_deploy_20261031/.venv/bin/python"
exec env -u LD_PRELOAD -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME \
  "$PY" "$ROOT/prepare_hardware.py" "$@"
