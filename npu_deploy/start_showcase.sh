#!/usr/bin/env bash
# Reuse prepared NPU evidence -> FPGA service -> reset -> hybrid execution.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="$ROOT/amd_hybrid_deploy_20261031"
if [[ -f "$ROOT/npu_deploy/fpga_board_mode.sh" ]]; then source "$ROOT/npu_deploy/fpga_board_mode.sh"; fi
FPGA_ADDRESS="${AIPC_FPGA_ADDRESS:-${AIPC_FPGA_SHADOW:-192.168.2.99:9009}}"
if [[ "${1:-}" == --panel && $# == 1 ]]; then
  PY="$ROOT/amd_hybrid_deploy_20261031/.venv/bin/python"
  set +e
  "$PY" "$ROOT/npu_deploy/showcase_panel.py" --workflow start
  code=$?
  set -e
  if [[ "$code" != 78 ]]; then exit "$code"; fi
  echo '启动面板不可用，继续使用原终端流程。' >&2
  set --
fi
panel_event() {
  if [[ -n "${AIPC_SHOWCASE_PANEL_TOKEN:-}" ]]; then
    printf '\nFLEXIFOLD_PANEL:%s:%s:%s\n' "$AIPC_SHOWCASE_PANEL_TOKEN" "$1" "$2"
  fi
}
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo '日常运行：复用 NPU 预检、确认 FPGA、复位、运行最多 750 步。折叠确认后自动归位，步数用完后可确认归位。'
  echo '用法：bash npu_deploy/start_showcase.sh [--check-only | --panel]'
  echo '正式运行分阶段按 Enter 确认；复位与任务会实际移动机械臂。输入 q 或按 Ctrl+C 退出。'
  echo '先运行准备脚本启动 FPGA；FPGA 地址可用 AIPC_FPGA_ADDRESS=IPv4:端口 指定。'
  exit 0
fi
if [[ "${1:-}" == --check-only && $# == 1 ]]; then
  echo "正式预览：NPU 预检 → FPGA FUI1 $FPGA_ADDRESS → 实际复位 → 最多 750 步任务 → 折叠确认后自动归位，步数用完后等待确认。"
  echo '不会访问硬件或修改配置。'; exit 0
fi
if (( $# )); then echo '未知参数；使用 --help。' >&2; exit 2; fi
if [[ -n "${AIPC_FPGA_COMMISSIONING_PLAN:-}${AIPC_FPGA_COMMISSIONING_SHA256:-}" ]]; then
  echo '受限策略试验请使用 commissioning.py run；日常入口包含额外复位，不消费试验计划。' >&2; exit 1
fi
mkdir -p "$ROOT/npu_deploy/artifacts/showcase"
LOCK_PATH="$ROOT/npu_deploy/artifacts/showcase/operation.lock"
if [[ -f "$ROOT/npu_deploy/operation_lock_path.py" ]]; then LOCK_PATH="$(python3 "$ROOT/npu_deploy/operation_lock_path.py")"; fi
mkdir -p "$(dirname -- "$LOCK_PATH")"
exec 9>"$LOCK_PATH"
if ! flock -n 9; then echo '任务或互动仍在运行，请先退出该流程。' >&2;exit 1;fi
if [[ -n "${AIPC_FPGA_AUTHORITY_CONFIG:-}" ]]; then
  "$PROJECT/.venv/bin/python" "$ROOT/npu_deploy/fpga_authority/check_config.py" --execute
fi
trap 'echo "已收到停止信号，脚本不再启动后续阶段。" >&2; exit 130' INT TERM

wait_for_enter() {
  local reply
  panel_event GATE "$2"
  while true; do
    printf '\n%s\n' "$1"
    printf '按 Enter 继续；输入 q 退出：'
    if ! IFS= read -r reply; then
      echo '输入结束，脚本退出。' >&2
      exit 130
    fi
    case "$reply" in
      '') return 0 ;;
      q|Q) echo '已退出，不再进入后续阶段。'; exit 130 ;;
      *) echo '请直接按 Enter，或输入 q 退出。' ;;
    esac
  done
}

echo '日常运行：复用准备结果 → FPGA 服务确认 → 复位 → 750 步任务；结束后保留 FPGA 服务。'
wait_for_enter '检查准备结果；完整平台展示可单独选择 AMD 自检。' platform
LIMIT="$(ulimit -l)"
if [[ "$LIMIT" != unlimited && "$LIMIT" -lt 1048576 ]]; then
  echo '调整本脚本及子进程的 memlock 上限为 1 GiB（不是预分配）；系统可能要求 sudo 密码。'
  sudo prlimit --pid $$ --memlock=1073741824:1073741824
fi
mkdir -p "$ROOT/npu_deploy/artifacts/showcase"
SESSION="$(mktemp -d "$ROOT/npu_deploy/artifacts/showcase/work_$(date +%Y%m%d_%H%M%S)_XXXXXX")"
echo "本次日志目录：$SESSION"
panel_event STAGE platform
echo '[1/4] 核对预算，复用本次开机的有效 NPU 预检'
python3 "$ROOT/npu_deploy/field_budget.py"
if ! python3 "$ROOT/npu_deploy/npu_preflight_reuse.py"; then
  bash "$ROOT/npu_deploy/run_npu_preflight.sh"
fi
wait_for_enter '准备结果已核对，准备确认 FPGA 服务。' fpga
panel_event STAGE fpga
echo '[2/4] 确认 FPGA 服务响应（FUI1）'
if [[ "${AIPC_FPGA_MODE:-b32}" == fpg1_v3 ]]; then
  if [[ -n "${AIPC_FPGA_AUTHORITY_CONFIG:-}" ]]; then
    "$PROJECT/.venv/bin/python" "$ROOT/npu_deploy/fpga_board/control_jupyter.py" ensure --isolated-lcd --bulk-vectors
  else
    "$PROJECT/.venv/bin/python" "$ROOT/npu_deploy/fpga_board/control_jupyter.py" ensure
  fi
fi
printf '$ python3 %q %q\n' "$ROOT/npu_deploy/fpga_service_probe.py" "$FPGA_ADDRESS"
if ! python3 "$ROOT/npu_deploy/fpga_service_probe.py" "$FPGA_ADDRESS"; then
  echo 'FPGA 服务未返回匹配确认；请在板端重新启动 host_observation 服务后重试。' >&2; exit 1
fi
echo 'FPGA 服务已响应；运行中的 FPG1 回包进一步显示真实 PL 检查结果。'
if [[ -n "${AIPC_FPGA_AUTHORITY_CONFIG:-}" ]]; then
  "$PROJECT/.venv/bin/python" "$ROOT/npu_deploy/fpga_authority/check_config.py" --execute --verify-service
fi
wait_for_enter 'FPGA 服务已响应。确认后将进入实际复位，机械臂会移动。' reset
panel_event STAGE reset
echo '[3/4] 实际复位：会使能并移动两臂'
echo '已确认复位；倒计时期间按 Ctrl+C 可取消。任务开始前还会再次等待 Enter。'
for n in 5 4 3 2 1; do echo "自动复位倒计时：$n"; sleep 1; done
env AIPC_RESET_AUTO_START=1 bash "$PROJECT/run.sh" reset --execute --trace 2>&1 | tee "$SESSION/reset.log"
if ! grep -q '^RESET_EXECUTION_COMPLETE$' "$SESSION/reset.log"; then
  echo '没有收到复位完成标记，不启动后续任务。' >&2; exit 1
fi
if [[ -n "${AIPC_FPGA_AUTHORITY_CONFIG:-}" ]]; then
  run_prompt='复位完成。确认后将正式运行最多 750 步，机械臂会移动。'
else
  run_prompt='复位完成。确认后运行最多 750 步。折叠确认后自动归位，步数用完后可确认归位。'
fi
wait_for_enter "$run_prompt" run
panel_event STAGE run
if [[ -n "${AIPC_FPGA_AUTHORITY_CONFIG:-}" ]]; then
  echo '[4/4] 正式执行 750 步 hybrid：NPU 视觉 + ROCm + FPGA 下发前核准 + 实时界面'
else
  echo '[4/4] 正式执行 750 步 hybrid：NPU 视觉 + ROCm + FPGA 旁路 + 实时界面'
fi
echo 'Ctrl+C 请求停止；控制器会尝试保持测得的当前位置，不会自动失使能。'
if [[ -n "${AIPC_FPGA_AUTHORITY_CONFIG:-}" ]]; then
  echo '本轮使用下发前逐条核准；FPGA 拒绝、失配或超时将中断策略下发，沿用原受控退出流程。'
else
  echo '任务启动前使用同一 FPG1 会话验证 PL；运行中 FPGA 是非阻塞旁路，不对动作或 watchdog 提供门控。'
fi
if [[ "${AIPC_FPGA_MODE:-b32}" == fpg1_v3 ]]; then
  export AIPC_FPGA_FOLD="$FPGA_ADDRESS"
else
  export AIPC_FPGA_SHADOW="$FPGA_ADDRESS"
fi
export AIPC_FPGA_REQUIRE_START=1
export AIPC_FPGA_AUTO_OFF="${AIPC_FPGA_AUTO_OFF:-0}"
if [[ -n "${AIPC_FPGA_AUTHORITY_CONFIG:-}" ]]; then
  exec bash "$ROOT/npu_deploy/run_hybrid_npu_fpga.sh" --steps 750 --execute --trace --preview --first-grasp-check
fi
export AIPC_FIRST_FOLD_RETRY_HOLD="${AIPC_FIRST_FOLD_RETRY_HOLD:-1}"
FIRST_GRASP_ARGS=()
if [[ "${AIPC_FIRST_GRASP_APPROACH:-0}" == 1 ]]; then FIRST_GRASP_ARGS+=(--first-grasp-approach); fi
bash "$ROOT/npu_deploy/run_hybrid_npu_fpga.sh" --steps 750 --execute --trace --preview --first-grasp-check \
  --finish-check --act-fp16 "${FIRST_GRASP_ARGS[@]}" 2>&1 | tee "$SESSION/task.log"
if ! grep -Fxq 'TOWEL_FINISH_HOLD' "$SESSION/task.log"; then
  if grep -Fxq 'TOWEL_FIRST_FOLD_HOLD' "$SESSION/task.log"; then
    wait_for_enter '首抓后毛巾仍摊开，双臂已保持当前位置。查看夹爪和毛巾后，可确认慢速归位再摆放。' return
  elif grep -Fxq 'TOWEL_BUDGET_HOLD' "$SESSION/task.log"; then
    wait_for_enter '任务已达到步数上限。请查看夹爪和毛巾，确认可以移动后再让双臂慢速归位。' return
  else
    exit 0
  fi
fi
panel_event STAGE return
echo '[归位] 双臂慢速返回任务起始位。'
# Keep the fold's FPGA session available for the operator outcome.
env -u AIPC_FPGA_FOLD -u AIPC_FPGA_SHADOW AIPC_RESET_AUTO_START=1 \
  bash "$PROJECT/run.sh" reset --execute --trace 2>&1 | tee "$SESSION/return.log"
if ! grep -Fxq 'RESET_EXECUTION_COMPLETE' "$SESSION/return.log"; then
  echo '未收到归位到位反馈。' >&2
  exit 1
fi
echo 'TOWEL_RETURN_COMPLETE'
