#!/usr/bin/env bash
set -eo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LIMIT="$(ulimit -l)"
if [[ "$LIMIT" != 1048576 ]]; then
  echo "当前终端锁定内存上限为 $LIMIT KiB，请先执行：sudo prlimit --pid \$\$ --memlock=1073741824:1073741824" >&2
  exit 1
fi
echo '三帧串行测试 ACT + guided 融合；scope 内存上限 12 GiB、CPU 配额 4 核、swap 为 0。设备共享内存未必全部计入。'
exec systemd-run --user --scope --collect --quiet \
  --property=MemoryMax=12G --property=MemorySwapMax=0 --property=CPUQuota=400% \
  env -u LD_PRELOAD -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME -u GLIBC_TUNABLES \
  HF_HOME="${HF_HOME:-/tmp/fpga-hf-cache}" OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
  bash "$ROOT/npu_deploy/episode_hybrid_inner.sh"
