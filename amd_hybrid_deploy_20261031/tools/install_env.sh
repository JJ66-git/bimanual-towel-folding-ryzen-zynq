#!/usr/bin/env bash
set -euo pipefail
DEPLOY_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_PROFILE="${1:-}"
if [[ "$DEPLOY_PROFILE" != cpu && "$DEPLOY_PROFILE" != rocm ]]; then
  echo 'Usage: bash tools/install_env.sh cpu|rocm [wheelhouse-directory]' >&2; exit 2
fi
cd "$DEPLOY_ROOT"
python3.12 -c 'import platform,sys; assert sys.version_info[:2]==(3,12) and platform.machine()=="x86_64" and sys.platform=="linux"'
if [[ -e .venv ]]; then echo '.venv 已存在；请使用一个新的解压目录，避免覆盖现有环境。' >&2; exit 2; fi
python3.12 -m venv .venv
if [[ -n "${2:-}" ]]; then
  .venv/bin/python -m pip install --no-index --find-links "$2" pip==25.3 setuptools==80.9.0 wheel==0.45.1
  if [[ "$DEPLOY_PROFILE" == cpu ]]; then
    .venv/bin/python -m pip install --no-index --find-links "$2" torch==2.7.0+cpu torchvision==0.22.0+cpu -r requirements/common.lock.txt
  else
    .venv/bin/python -m pip install --no-index --find-links "$2" -r requirements/rocm-gfx1152.txt -r requirements/common.lock.txt
  fi
else
  .venv/bin/python -m pip install pip==25.3 setuptools==80.9.0 wheel==0.45.1
  if [[ "$DEPLOY_PROFILE" == cpu ]]; then
    .venv/bin/python -m pip install torch==2.7.0 torchvision==0.22.0 --index-url https://download.pytorch.org/whl/cpu
  else
    .venv/bin/python -m pip install --index-url https://repo.amd.com/rocm/whl-multi-arch/ -r requirements/rocm-gfx1152.txt
  fi
  .venv/bin/python -m pip install -r requirements/common.lock.txt -c "requirements/$DEPLOY_PROFILE.constraints.txt"
fi
.venv/bin/python -m pip check
mkdir -p validation
.venv/bin/python -m pip freeze > "validation/installed-$DEPLOY_PROFILE.txt"
./run.sh doctor --backend "$DEPLOY_PROFILE"
