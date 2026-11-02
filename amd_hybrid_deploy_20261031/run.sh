#!/usr/bin/env bash
set -euo pipefail
DEPLOY_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_PYTHON="${OWN_PYTHON:-$DEPLOY_ROOT/.venv/bin/python}"
if [[ ! -x "$DEPLOY_PYTHON" ]]; then
  echo '先按 docs/INSTALL_ZH.md 创建本包 .venv；或用 OWN_PYTHON 指定专用 Python 3.12。' >&2
  exit 1
fi
export PYTHONPATH="$DEPLOY_ROOT/piper_towel_replica/runtime_source/src:$DEPLOY_ROOT/piper_towel_replica/deployment_entry:$DEPLOY_ROOT/piper_towel_replica/upstream/act_reference/src:$DEPLOY_ROOT/shared_runtime"
export PATH="$(dirname -- "$DEPLOY_PYTHON"):$PATH"
export TORCH_HOME="$DEPLOY_ROOT/piper_towel_replica/deployment_assets/torch"
export HF_HOME="$DEPLOY_ROOT/cache/huggingface"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONUNBUFFERED=1 PIPER_ACTION_FILTER_ALPHA=1.0
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}" MKL_NUM_THREADS="${MKL_NUM_THREADS:-4}"
case "${1:-}" in
  doctor|benchmark|bind|verify)
    DEPLOY_TOOL="$1"; shift
    exec "$DEPLOY_PYTHON" "$DEPLOY_ROOT/tools/$DEPLOY_TOOL.py" "$@" ;;
  prepare)
    shift; exec "$DEPLOY_PYTHON" "$DEPLOY_ROOT/piper_towel_replica/scripts/prepare_day.py" "$@" ;;
  cameras)
    shift; exec "$DEPLOY_PYTHON" "$DEPLOY_ROOT/piper_towel_replica/scripts/prepare_cobot.py" "$@" ;;
  *) exec "$DEPLOY_PYTHON" "$DEPLOY_ROOT/piper_towel_replica/scripts/run_cobot.py" "$@" ;;
esac
