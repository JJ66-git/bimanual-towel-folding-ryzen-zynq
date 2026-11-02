#!/usr/bin/env bash
# Run only the offline comparison in a limited user scope on this 32 GB host.
set -eo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LIMIT="$(ulimit -l)"
if [[ "$LIMIT" == unlimited ]]; then
  echo '当前终端锁定内存上限为 unlimited。请先在该终端执行：sudo prlimit --pid $$ --memlock=1073741824:1073741824' >&2
  exit 1
fi
if [[ "$LIMIT" -lt 1048576 ]]; then
  echo "当前终端锁定内存上限为 $LIMIT KiB。请先在该终端执行：sudo prlimit --pid \$\$ --memlock=1073741824:1073741824" >&2
  exit 1
fi
if [[ "$LIMIT" -gt 1048576 ]]; then
  echo "当前终端锁定内存上限为 $LIMIT KiB。请先将其调整为 1 GiB：sudo prlimit --pid \$\$ --memlock=1073741824:1073741824" >&2
  exit 1
fi
echo '将离线测试放在 systemd 用户 scope：CPU 4 核，内存 12 GiB，禁用该 scope 的 swap。'
echo '设备驱动的全部共享内存未必都计入该限制。'
exec systemd-run --user --scope --collect --quiet \
  --property=MemoryMax=12G --property=MemorySwapMax=0 --property=CPUQuota=400% \
  bash "$ROOT/npu_deploy/run_policy_check.sh" "$@"
