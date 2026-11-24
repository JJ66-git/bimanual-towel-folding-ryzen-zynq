#!/usr/bin/env bash
# One standalone NPU encoder check with bounded resources and visible progress.
set -eo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/npu_deploy/runtime_resources.sh"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo '用法：bash npu_deploy/run_npu_preflight.sh'
  echo '仅运行固定图片的 NPU 视觉编码，不访问机器人或 FPGA；首次编译可能需要几分钟。'
  echo '先在当前终端运行：sudo prlimit --pid $$ --memlock=1073741824:1073741824'
  exit 0
fi
if (( $# )); then echo '此检查不接受参数。' >&2; exit 2; fi
LIMIT="$(ulimit -l)"
if [[ "$LIMIT" != unlimited && "$LIMIT" -lt 1048576 ]]; then
  echo '请先在当前终端运行：sudo prlimit --pid $$ --memlock=1073741824:1073741824' >&2; exit 1
fi
ART="$ROOT/npu_deploy/artifacts/smol_vision"
for path in "$ART/smol_vision.onnx" "$ART/fixture.npz" "$ROOT/ryzen-ai-npu/venv/bin/python" /opt/xilinx/xrt/setup.sh; do
  if [[ ! -e "$path" ]]; then echo "缺少：$path" >&2; exit 1; fi
done
mkdir -p "$ROOT/npu_deploy/artifacts/npu_preflight"
SESSION="$(mktemp -d "$ROOT/npu_deploy/artifacts/npu_preflight/run_$(date +%Y%m%d_%H%M%S)_XXXXXX")"
ln -s "$ART/smol_vision.onnx" "$SESSION/smol_vision.onnx"
ln -s "$ART/fixture.npz" "$SESSION/fixture.npz"
if [[ -f "$ART/fixture_metadata.json" ]]; then ln -s "$ART/fixture_metadata.json" "$SESSION/fixture_metadata.json"; fi
LOG="$SESSION/npu.log"
MEMORY_MAX="$AIPC_RUNTIME_MEMORY_MAX"
echo "NPU 独立检查，完整日志：$LOG"
echo "首次编译可能需要几分钟；看到 compile/cache 日志后请等待结果。内存上限 $MEMORY_MAX、CPU $AIPC_RUNTIME_CPU_QUOTA（全部 $AIPC_RUNTIME_CPUS 个逻辑核可用）、swap 0；设备共享内存未必计入。"
set +e
systemd-run --user --scope --collect --quiet \
  "--property=MemoryMax=$MEMORY_MAX" --property=MemorySwapMax=0 "--property=CPUQuota=$AIPC_RUNTIME_CPU_QUOTA" \
  env -u LD_PRELOAD -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME -u GLIBC_TUNABLES \
  OMP_NUM_THREADS="$OMP_NUM_THREADS" MKL_NUM_THREADS="$MKL_NUM_THREADS" OPENBLAS_NUM_THREADS="$OPENBLAS_NUM_THREADS" \
  bash "$ROOT/npu_deploy/demo_npu_job.sh" "$SESSION" 2>&1 | tee "$LOG" | \
  grep --line-buffered -Ei 'compile|cache|Actually running on NPU|NPU_EXECUTION_CONFIRMED|FIXTURE_SOURCE=|IMAGE_RESULT=|CHECK_REPORT=|error|fatal|traceback|failed'
RESULT=${PIPESTATUS[0]}
set -e
echo "进程退出状态：$RESULT；完整日志：$LOG"
if (( RESULT != 0 )); then exit "$RESULT"; fi
if ! grep -Eq 'Actually running on NPU[[:space:]]+[1-9][0-9]*|^NPU_EXECUTION_CONFIRMED provider=VitisAIExecutionProvider kernels=[1-9][0-9]*' "$LOG"; then
  echo '未检测到 NPU 子图，不能将本次 CPU 回退视为 NPU 检查通过。' >&2; exit 1
fi
if [[ "$(grep -c '^IMAGE_RESULT=' "$LOG" || true)" != 3 ]]; then
  echo '未找到预期的三路图像结果。' >&2; exit 1
fi
echo 'NPU_CHECK_PASSED: 本次日志有 NPU 子图和三路有效输出；参考来源见 FIXTURE_SOURCE，误差见 CHECK_REPORT，不代表现场精度验收。'
python3 - "$ART" "$SESSION" "$ROOT/npu_deploy" <<'PY'
import datetime,hashlib,json,sys
from pathlib import Path
art,session=map(Path,sys.argv[1:3])
sys.path.insert(0,sys.argv[3])
from npu_preflight_reuse import runtime_fingerprint
def digest(path):
    with path.open('rb') as src:return hashlib.file_digest(src,'sha256').hexdigest()
report={'passed':True,'checked_at':datetime.datetime.now().astimezone().isoformat(),'session':str(session),
        'runtime_fingerprint':runtime_fingerprint(),
        'onnx_sha256':digest(art/'smol_vision.onnx'),'fixture_sha256':digest(art/'fixture.npz'),
        'log_sha256':digest(session/'npu.log'),'npu_partition_confirmed':True,'valid_image_outputs':3,
        'fixture_source':json.loads((art/'fixture_metadata.json').read_text()).get('source_kind','UNSPECIFIED') if (art/'fixture_metadata.json').is_file() else 'UNSPECIFIED',
        'qualification':'Execution/finite-output check only; numerical differences in session/check_npu.json; no field accuracy certification.'}
(session.parent/'latest_success.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
PY
