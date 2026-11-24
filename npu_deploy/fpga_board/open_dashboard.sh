#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="$ROOT/amd_hybrid_deploy_20261031/.venv/bin/python"
if ! systemctl --user is-active --quiet flexifold-board-dashboard.service; then
  systemd-run --user --collect --unit=flexifold-board-dashboard --property=MemoryMax=256M \
    --working-directory="$ROOT" "$PYTHON" "$ROOT/npu_deploy/fpga_board/serve_dashboard.py"
fi
if [[ "${1:-}" != --no-browser ]]; then xdg-open http://127.0.0.1:8765 >/dev/null 2>&1 || true;fi
printf '%s\n' 'FPGA协同可视化：http://127.0.0.1:8765 （只读，不发送机器人动作）'
