#!/usr/bin/env bash
# Invoked inside the bounded user scope by run_episode_check.sh.
set -eo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/amd_hybrid_deploy_20261031/.venv/bin/python"
ART="$ROOT/npu_deploy/artifacts/smol_vision"
for frame in 0000 0450 0899; do
  FIXTURE="$ROOT/npu_deploy/artifacts/episode9/fixtures/frame_$frame/observation.json"
  LOG="$ART/episode9_frame_$frame.log"
  echo "开始第 $((10#$frame)) 帧（单进程串行）"
  set +e
  "$PY" "$ROOT/npu_deploy/compare_smol_policy.py" --backend rocm --worker npu --warmup 0 --repeat 1 \
    --fixture "$FIXTURE" --output-stem "episode9_frame_$frame" >"$LOG" 2>&1
  status=$?
  set -e
  if [[ $status -ne 0 ]]; then
    echo "第 $((10#$frame)) 帧失败，退出状态 $status；日志：$LOG"
    tail -25 "$LOG"
    exit "$status"
  fi
  if ! grep -q 'Actually running on NPU' "$LOG"; then
    echo "第 $((10#$frame)) 帧已生成报告，但未找到 NPU 执行日志：$LOG" >&2
    exit 1
  fi
  "$PY" - "$ART/episode9_frame_$frame.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
g = d['guided']; v = g['max_abs_per_action_dim']
print('FRAME_RESULT=' + json.dumps({'fixture': d['fixture'], 'npu_confirmed_by_log': True,
    'guided_max_joint_diff_rad': max(v[i] for i in (*range(6), *range(7,13))),
    'guided_gripper_diffs_m': [v[6], v[13]], 'native_s': g['native_seconds'][0],
    'npu_s': g['worker_seconds'][0], 'guided_grad_calls': [d['native_guided_autograd_calls'], d['worker_guided_autograd_calls']]}), flush=True)
PY
done
echo "三帧串行对照完成；详细报告与日志目录：$ART"
