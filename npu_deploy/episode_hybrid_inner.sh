#!/usr/bin/env bash
set -eo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/amd_hybrid_deploy_20261031/.venv/bin/python"
ART="$ROOT/npu_deploy/artifacts/episode9"
for frame in 0000 0450 0899; do
  fixture="$ART/fixtures/frame_$frame/observation.json"
  log="$ART/hybrid_frame_$frame.log"
  report="$ART/hybrid_frame_$frame.json"
  echo "开始第 $((10#$frame)) 帧的 ACT 与 SmolVLA 融合对照"
  set +e
  "$PY" "$ROOT/npu_deploy/check_hybrid_offline.py" --steps 1 --fixture "$fixture" --out "$report" >"$log" 2>&1
  status=$?
  set -e
  if [[ $status -ne 0 ]]; then
    echo "第 $((10#$frame)) 帧未完成，退出状态 $status；日志：$log"
    tail -25 "$log"
    exit "$status"
  fi
  if ! grep -q 'Actually running on NPU' "$log"; then
    echo "第 $((10#$frame)) 帧缺少 NPU 执行记录：$log" >&2
    exit 1
  fi
  "$PY" - "$report" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
a, b = d['native']['rows'][0], d['npu']['rows'][0]
print('HYBRID_FRAME=' + json.dumps({'fixture': d['fixture'], 'native_source': a['source'],
  'native_reason': a['detail'], 'npu_source': b['source'], 'npu_reason': b['detail'],
  'max_target_abs_difference_per_dim': d['max_target_abs_difference_per_dim'],
  'native_guided_s': d['native_guided_s'], 'npu_guided_s': d['npu_guided_s']}), flush=True)
PY
done
echo "三帧 ACT + guided 融合离线对照完成，报告目录：$ART"
