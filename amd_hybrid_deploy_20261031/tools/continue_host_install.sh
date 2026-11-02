#!/usr/bin/env bash
# Continue the existing Python 3.12 environment on the actual host.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
python="$root/.venv/bin/python"
if [[ ! -x "$python" ]]; then
  echo '找不到现有 .venv；请先按照 docs/INSTALL_ZH.md 准备 Python 3.12 环境。' >&2
  exit 1
fi
"$python" -c 'import platform,sys; assert sys.version_info[:2] == (3,12) and platform.machine() == "x86_64" and sys.platform == "linux"'
"$python" tools/verify.py

missing=()
command -v candump >/dev/null || missing+=(can-utils)
command -v v4l2-ctl >/dev/null || missing+=(v4l-utils)
if ((${#missing[@]})); then
  echo "需要安装系统工具: ${missing[*]}"
  sudo apt update
  sudo apt install -y "${missing[@]}"
fi

export PIP_CACHE_DIR="$root/.tooling/pip-cache"
"$python" -m pip install pip==25.3 setuptools==80.9.0 wheel==0.45.1
"$python" -m pip install --index-url https://repo.amd.com/rocm/whl-multi-arch/ -r requirements/rocm-gfx1152.txt
"$python" -m pip install -r requirements/common.lock.txt -c requirements/rocm.constraints.txt
"$python" -m pip check
mkdir -p validation
"$python" -m pip freeze > validation/installed-rocm.txt
./run.sh doctor --backend rocm --out validation/target-doctor.json
./run.sh benchmark --backend rocm --warmup 2 --repeat 20 --threads 4 --out validation/target-rocm.json
echo '安装及离线验证结束；请检查 validation/target-rocm.json 中的 passed 字段。'
