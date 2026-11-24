#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo 'Install the Linux Python 3.12 policy environment for Ryzen AI HX 370 (gfx1150).'
  exit 0
fi
if (( $# )); then exit 2; fi
if [[ -e "$root/.venv" || -L "$root/.venv" ]]; then
  echo "An environment already exists at $root/.venv" >&2
  exit 1
fi
python3.12 -m venv "$root/.venv"
python="$root/.venv/bin/python"
"$python" -m pip install pip==25.3 setuptools==80.9.0 wheel==0.45.1
"$python" -m pip install --index-url https://repo.amd.com/rocm/whl-multi-arch/ \
  'rocm[libraries,device-gfx1150]==7.14.1' \
  'torch[device-gfx1150]==2.11.0+rocm7.14.1' \
  'torchvision[device-gfx1150]==0.26.0+rocm7.14.1'
"$python" -m pip install -r "$root/requirements/common.lock.txt" \
  -c "$root/requirements/rocm.constraints.txt"
"$python" -m pip check
