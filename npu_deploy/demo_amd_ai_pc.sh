#!/usr/bin/env bash
# Guided, live device and inference demonstration; no robot or FPGA access.
set -eo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="$ROOT/amd_hybrid_deploy_20261031"
ART="$ROOT/npu_deploy/artifacts/smol_vision"
DEMO_MEMORY_MAX="${AMD_DEMO_MEMORY_MAX:-16G}"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  printf '%s\n' '交互式演示：AMD CPU、AMD-SMI、ROCm GPU 真实计算、XRT NPU 自检、SmolVLA 视觉模型 NPU 推理。' \
    '用法：bash npu_deploy/demo_amd_ai_pc.sh' \
    '开始前在同一终端设置：sudo prlimit --pid $$ --memlock=1073741824:1073741824'
  exit 0
fi
if [[ $# -ne 0 ]]; then echo '未知参数；使用 --help 查看用法。' >&2; exit 2; fi
LIMIT="$(ulimit -l)"
if [[ "$LIMIT" != unlimited && "$LIMIT" -lt 1048576 ]]; then
  echo "当前终端的锁定内存上限为 $LIMIT KiB。请先在同一终端执行：" >&2
  echo 'sudo prlimit --pid $$ --memlock=1073741824:1073741824' >&2
  exit 1
fi
for path in "$PROJECT/.venv/bin/amd-smi" "$PROJECT/.venv/bin/python" \
            "$ROOT/ryzen-ai-npu/venv/bin/python" /opt/xilinx/xrt/setup.sh \
            "$ART/smol_vision.onnx" "$ART/fixture.npz"; do
  if [[ ! -e "$path" ]]; then echo "缺少演示所需文件：$path" >&2; exit 1; fi
done
mkdir -p "$ROOT/npu_deploy/artifacts/demo"
SESSION="$(mktemp -d "$ROOT/npu_deploy/artifacts/demo/live_$(date +%Y%m%d_%H%M%S)_XXXXXX")"
pause() {
  local reply
  printf '\n%s\n' '按 Enter 继续，输入 q 退出。'
  if ! IFS= read -r reply; then echo '输入结束，演示退出。'; exit 0; fi
  if [[ "$reply" == q || "$reply" == Q ]]; then echo '演示已退出。'; exit 0; fi
}
show_command() { printf '$'; printf ' %q' "$@"; printf '\n'; }

echo 'AMD AI PC 现场演示：每步显示真实命令；不会连接相机、机械臂或 FPGA。'
echo "本次独立日志：$SESSION"
echo "锁定内存上限：$LIMIT KiB（unlimited 表示无限）；这是上限，不是预先分配的内存。"
pause

echo; echo '[1/4] 机器与设备：现场读取 CPU 和 PCI 信息'
echo '$ date -Is'
date -Is
echo '$ lscpu | grep "Model name"'
lscpu | grep 'Model name'
echo '$ lspci | grep -Ei "VGA compatible|display controller|signal processing controller"'
lspci | grep -Ei 'VGA compatible|display controller|signal processing controller'
pause

echo; echo '[2/4] AMD-SMI 和真实 ROCm 矩阵计算'
AMD_CMD=(env -u LD_PRELOAD -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME -u GLIBC_TUNABLES
  "$PROJECT/.venv/bin/amd-smi")
show_command "${AMD_CMD[@]}"
"${AMD_CMD[@]}"
GPU_CMD=(env -u LD_PRELOAD -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME -u GLIBC_TUNABLES \
  OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
  "$PROJECT/.venv/bin/python" "$ROOT/npu_deploy/demo_rocm_compute.py")
show_command "${GPU_CMD[@]}"
"${GPU_CMD[@]}"
pause

echo; echo '[3/4] XRT 检查 NPU 并运行硬件自检'
echo '$ (unset LD_PRELOAD LD_LIBRARY_PATH PYTHONPATH PYTHONHOME GLIBC_TUNABLES; source /opt/xilinx/xrt/setup.sh; xrt-smi examine) 2>&1 | tee <本次设备日志>'
if ! ( unset LD_PRELOAD LD_LIBRARY_PATH PYTHONPATH PYTHONHOME GLIBC_TUNABLES
       source /opt/xilinx/xrt/setup.sh
       xrt-smi examine ) 2>&1 | tee "$SESSION/xrt_examine.log"; then
  echo 'XRT 设备检查失败。' >&2; exit 1
fi
if ! grep -Eq '^\|.*NPU.*\|' "$SESSION/xrt_examine.log"; then
  echo 'XRT 未在设备表中显示 NPU，请检查上面的实际输出。' >&2; exit 1
fi
echo '$ (unset LD_PRELOAD LD_LIBRARY_PATH PYTHONPATH PYTHONHOME GLIBC_TUNABLES; source /opt/xilinx/xrt/setup.sh; xrt-smi validate) 2>&1 | tee <本次自检日志>'
if ! ( unset LD_PRELOAD LD_LIBRARY_PATH PYTHONPATH PYTHONHOME GLIBC_TUNABLES
       source /opt/xilinx/xrt/setup.sh
       xrt-smi validate ) 2>&1 | tee "$SESSION/xrt_validate.log"; then
  echo 'XRT NPU 自检命令失败。' >&2; exit 1
fi
PASSED="$(grep -Ec 'Test Status[[:space:]]*:[[:space:]]*\[PASSED\]' "$SESSION/xrt_validate.log" || true)"
if (( PASSED < 3 )); then echo "NPU 自检通过项不足 3 项（实际 $PASSED），请检查日志。" >&2; exit 1; fi
pause

echo; echo '[4/4] 验证模型来源并实时执行 NPU 视觉推理'
show_command python3 "$ROOT/npu_deploy/demo_verify_artifacts.py"
python3 "$ROOT/npu_deploy/demo_verify_artifacts.py"
ln -s "$ART/smol_vision.onnx" "$SESSION/smol_vision.onnx"
ln -s "$ART/fixture.npz" "$SESSION/fixture.npz"
echo "以下命令将启动 NPU 推理，scope 内存上限 $DEMO_MEMORY_MAX、CPU 配额 4 核、swap 为 0。"
echo '设备驱动共享内存未必全部计入；首次编译可能需要几分钟。'
NPU_CMD=(systemd-run --user --scope --collect --quiet \
  "--property=MemoryMax=$DEMO_MEMORY_MAX" --property=MemorySwapMax=0 --property=CPUQuota=400% \
  env -u LD_PRELOAD -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME -u GLIBC_TUNABLES \
  OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
  bash "$ROOT/npu_deploy/demo_npu_job.sh" "$SESSION")
show_command "${NPU_CMD[@]}"
echo "输出重定向到：$SESSION/npu_inference.log"
echo "详细实时日志：$SESSION/npu_inference.log"
set +e
"${NPU_CMD[@]}" >"$SESSION/npu_inference.log" 2>&1
STATUS=$?
set -e
echo "NPU 推理进程退出状态：$STATUS"
grep -E 'Actually running on NPU|IMAGE_RESULT=|CHECK_REPORT=|ERROR|Error|Traceback|Failed' \
  "$SESSION/npu_inference.log" | tail -25 || true
if (( STATUS != 0 )); then echo '模型推理失败，检查上面的日志路径。' >&2; exit "$STATUS"; fi
if ! grep -Eq 'Actually running on NPU[[:space:]]+[1-9][0-9]*' "$SESSION/npu_inference.log"; then
  echo '未找到 NPU 实际执行子图的日志，不能将此次结果认定为 NPU 推理。' >&2; exit 1
fi
IMAGE_COUNT="$(grep -c '^IMAGE_RESULT=' "$SESSION/npu_inference.log" || true)"
if (( IMAGE_COUNT != 3 )); then echo "预期 3 路图像的推理结果，实际找到 $IMAGE_COUNT 路。" >&2; exit 1; fi
echo; echo '现场演示完成：结果来自本次命令，模型部分为离线视觉编码器。'
echo "查看源码：$ROOT/npu_deploy/demo_rocm_compute.py 和 $ROOT/npu_deploy/demo_npu_job.sh"
echo "查看完整日志与本次生成的结果：$SESSION"
