#!/usr/bin/env bash
# Reset only. The native controller still verifies identity and motion limits.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="$ROOT/amd_hybrid_deploy_20261031"
if [[ -f "$ROOT/npu_deploy/fpga_board_mode.sh" ]]; then source "$ROOT/npu_deploy/fpga_board_mode.sh"; fi
if [[ "${1:-}" == --panel && $# == 1 ]]; then
  set +e
  "$PROJECT/.venv/bin/python" "$ROOT/npu_deploy/showcase_panel.py" --workflow reset
  code=$?
  set -e
  if [[ "$code" != 78 ]]; then exit "$code"; fi
  echo '启动面板不可用，继续使用原终端流程。' >&2
  set --
fi
if [[ "${1:-}" == --check-only && $# == 1 ]]; then
  echo '仅复位：确认 → 5 秒倒计时 → 双臂复位与轨迹记录；向已有 FPGA 服务发送显示状态，不启动任务、不重新准备相机或 NPU。'
  exit 0
fi
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo '用法：bash npu_deploy/reset_showcase.sh [--panel | --check-only]'
  exit 0
fi
if (( $# )); then echo '未知参数。' >&2;exit 2;fi
mkdir -p "$ROOT/npu_deploy/artifacts/showcase"
LOCK_PATH="$ROOT/npu_deploy/artifacts/showcase/operation.lock"
if [[ -f "$ROOT/npu_deploy/operation_lock_path.py" ]]; then LOCK_PATH="$(python3 "$ROOT/npu_deploy/operation_lock_path.py")"; fi
mkdir -p "$(dirname -- "$LOCK_PATH")"
exec 9>"$LOCK_PATH"
if ! flock -n 9; then echo '任务或互动仍在运行，请先退出该流程。' >&2;exit 1;fi
trap 'echo "复位已中断，不启动任务。" >&2;exit 130' INT TERM
if [[ -n "${AIPC_SHOWCASE_PANEL_TOKEN:-}" ]]; then
  printf '\nFLEXIFOLD_PANEL:%s:GATE:reset\n' "$AIPC_SHOWCASE_PANEL_TOKEN"
fi
echo '仅复位：确认后会使能并移动双臂。按 Enter 继续，输入 q 退出。'
IFS= read -r reply || exit 130
if [[ -n "$reply" ]]; then echo '已退出复位。';exit 130;fi
if [[ -n "${AIPC_SHOWCASE_PANEL_TOKEN:-}" ]]; then
  printf '\nFLEXIFOLD_PANEL:%s:STAGE:reset\n' "$AIPC_SHOWCASE_PANEL_TOKEN"
fi
for n in 5 4 3 2 1; do echo "复位倒计时：$n";sleep 1;done
mkdir -p "$ROOT/npu_deploy/artifacts/showcase"
SESSION="$(mktemp -d "$ROOT/npu_deploy/artifacts/showcase/reset_$(date +%Y%m%d_%H%M%S)_XXXXXX")"
echo "复位日志目录：$SESSION"
env AIPC_RESET_AUTO_START=1 bash "$PROJECT/run.sh" reset --execute --trace 2>&1 | tee "$SESSION/reset.log"
if ! grep -q '^RESET_EXECUTION_COMPLETE$' "$SESSION/reset.log"; then
  echo '未收到复位完成标记，请检查本次复位日志。' >&2;exit 1
fi
echo 'RESET_ONLY_COMPLETE: 复位完成；未启动任务，FPGA 服务保持原状态。'
