#!/usr/bin/env bash
# Experimental mixed ROCm/NPU entry; defaults to the original read-only mode.
set -eo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK_ONLY=0
for arg in "$@"; do
  if [[ "$arg" == --check-only ]]; then CHECK_ONLY=1; fi
done
LIMIT="$(ulimit -l)"
if [[ "$CHECK_ONLY" == 0 && "$LIMIT" != unlimited && "$LIMIT" -lt 1048576 ]]; then
  echo "锁定内存上限为 $LIMIT KiB，请在当前终端先运行：sudo prlimit --pid \$\$ --memlock=1073741824:1073741824" >&2
  exit 1
fi
exec env -u LD_PRELOAD -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME -u GLIBC_TUNABLES \
  "$ROOT/amd_hybrid_deploy_20261031/.venv/bin/python" "$ROOT/npu_deploy/launch_hybrid.py" "$@"
