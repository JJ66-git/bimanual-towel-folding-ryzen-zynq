#!/usr/bin/env bash
# Optional user-local Ubuntu 24.04 / Python 3.12 Tk runtime. No sudo.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT/amd_hybrid_deploy_20261031/.ui-runtime"
CACHE="$TARGET/packages"
mkdir -p "$CACHE"
cd "$CACHE"
apt-get download python3-tk libtk8.6 tk8.6-blt2.5 libxss1
for package in ./*.deb; do dpkg-deb -x "$package" "$TARGET"; done
PY="$ROOT/amd_hybrid_deploy_20261031/.venv/bin/python"
"$PY" - "$ROOT" <<'PY'
from pathlib import Path
import sys
sys.path.insert(0,str(Path(sys.argv[1])/'amd_hybrid_deploy_20261031'))
from shared_runtime.tk_support import load_tk
print('Tk version:',load_tk().TkVersion)
PY
