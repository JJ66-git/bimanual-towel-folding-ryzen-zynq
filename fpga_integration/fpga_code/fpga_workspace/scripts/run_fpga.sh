#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 3 ]]; then
  echo "Usage: bash scripts/run_fpga.sh PC_IPV4 synthetic_test|recorded_replay|host_observation NEW_RUN_DIR" >&2
  exit 2
fi
runtime_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$runtime_root"
exec /usr/local/share/pynq-venv/bin/python3 -B -u -m board.runtime --peer "$1" --source "$2" --out "$3"
