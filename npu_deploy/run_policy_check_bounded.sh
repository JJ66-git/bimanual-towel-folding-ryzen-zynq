#!/usr/bin/env bash
# Run only the offline comparison in a limited user scope on this 32 GB host.
set -eo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/npu_deploy/runtime_resources.sh"
LIMIT="$(ulimit -l)"
if [[ "$LIMIT" != unlimited && "$LIMIT" -lt 1048576 ]]; then
  echo '锁定内存不足1GiB，请先调整当前终端memlock。' >&2
  exit 1
fi
echo "离线测试 scope：CPU $AIPC_RUNTIME_CPU_QUOTA，内存 $AIPC_RUNTIME_MEMORY_MAX，swap 0。"
echo '设备驱动的全部共享内存未必都计入该限制。'
exec systemd-run --user --scope --collect --quiet \
  "--property=MemoryMax=$AIPC_RUNTIME_MEMORY_MAX" --property=MemorySwapMax=0 "--property=CPUQuota=$AIPC_RUNTIME_CPU_QUOTA" \
  bash "$ROOT/npu_deploy/run_policy_check.sh" "$@"
