#!/usr/bin/env bash
# Explicit matched-version selection; no packet negotiation or silent downgrade.
if [[ -n "${AIPC_FPGA_FOLD:-}" && -n "${AIPC_FPGA_SHADOW:-}" ]]; then
  echo '不能同时设置 AIPC_FPGA_FOLD 和 AIPC_FPGA_SHADOW。' >&2
  return 2
fi
AIPC_FPGA_MODE="${AIPC_FPGA_MODE:-$(python3 - "$ROOT/npu_deploy/fpga_board/active_profile.json" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]);print(json.loads(p.read_text()).get('mode','b32') if p.is_file() else 'b32')
PY
)}"
if [[ "$AIPC_FPGA_MODE" == fpg1_v3 ]]; then
  if [[ -n "${AIPC_FPGA_SHADOW:-}" ]]; then echo 'v3 模式不能使用 B32 的 AIPC_FPGA_SHADOW；请选择匹配协议。' >&2;return 2;fi
  export AIPC_FPGA_FOLD="${AIPC_FPGA_FOLD:-${AIPC_FPGA_ADDRESS:-192.168.2.99:9009}}"
  export AIPC_FPGA_ADDRESS="$AIPC_FPGA_FOLD"
  export AIPC_FPGA_SOURCE="${AIPC_FPGA_SOURCE:-HOST_OBSERVATION}"
elif [[ "$AIPC_FPGA_MODE" == b32 ]]; then
  if [[ -n "${AIPC_FPGA_FOLD:-}" ]]; then echo 'B32 模式不能使用 AIPC_FPGA_FOLD。' >&2;return 2;fi
  export AIPC_FPGA_SHADOW="${AIPC_FPGA_SHADOW:-${AIPC_FPGA_ADDRESS:-192.168.2.99:9009}}"
  export AIPC_FPGA_ADDRESS="$AIPC_FPGA_SHADOW"
else
  echo 'AIPC_FPGA_MODE 只能是 fpg1_v3 或 b32。' >&2;return 2
fi
export AIPC_FPGA_MODE
