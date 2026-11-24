#!/usr/bin/env bash
# The shared panel owns the window; the booth backend stays headless.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -n "${AIPC_SHOWCASE_PANEL_TOKEN:-}" ]]; then
  printf '\nFLEXIFOLD_PANEL:%s:STAGE:interactive\n' "$AIPC_SHOWCASE_PANEL_TOKEN"
fi
exec bash "$ROOT/flexifold_booth_first_grasp/start_booth_hardware.sh" --no-browser --port 8766 --auto-leader-can
