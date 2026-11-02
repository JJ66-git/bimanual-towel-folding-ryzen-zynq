#!/usr/bin/env bash
set -euo pipefail
DEPLOY_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DEPLOY_ROOT"
DEPLOY_PROFILE="${1:-}"
if [[ "$DEPLOY_PROFILE" != cpu && "$DEPLOY_PROFILE" != rocm ]]; then echo 'Usage: bash tools/download_wheels.sh cpu|rocm' >&2; exit 2; fi
# Run after the matching install_env.sh on an online Linux x86_64 Python 3.12 staging machine.
mkdir -p "wheelhouse-$DEPLOY_PROFILE"
if [[ "$DEPLOY_PROFILE" == cpu ]]; then
  .venv/bin/python -m pip download --only-binary=:all: -d "wheelhouse-$DEPLOY_PROFILE" torch==2.7.0 torchvision==0.22.0 --index-url https://download.pytorch.org/whl/cpu
else
  .venv/bin/python -m pip download --only-binary=:all: -d "wheelhouse-$DEPLOY_PROFILE" --index-url https://repo.amd.com/rocm/whl-multi-arch/ -r requirements/rocm-gfx1152.txt
fi
.venv/bin/python -m pip download --only-binary=:all: -d "wheelhouse-$DEPLOY_PROFILE" pip==25.3 setuptools==80.9.0 wheel==0.45.1 -r requirements/common.lock.txt
.venv/bin/python -m pip freeze > "wheelhouse-$DEPLOY_PROFILE/installed-freeze.txt"
