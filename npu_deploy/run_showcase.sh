#!/usr/bin/env bash
# Direct AI PC menu: current hardware demo, read-only inventory and previews.
set -eo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="$ROOT/amd_hybrid_deploy_20261031"
PY="$PROJECT/.venv/bin/python"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  printf '%s\n' 'AMD AI PC 直连演示：设备验证、USB/CAN 盘点、相机诊断、FPGA 网络及合成协议检查、控制命令预览。' \
    '用法：bash npu_deploy/run_showcase.sh' \
    '该菜单不执行机械臂复位或动作；真实连接和控制仍待验证。'
  exit 0
fi
if (( $# )); then echo '未知参数；使用 --help 查看用法。' >&2; exit 2; fi
echo 'AMD AI PC 直连演示入口'
echo '菜单只检查与预览，不发送机械臂动作。'
while true; do
  printf '\n'
  printf '%s\n' \
    '1  AMD AI PC 与模型现场演示' \
    '2  直连依赖、USB、CAN 和相机节点盘点' \
    '3  只读诊断相机身份和支持的格式' \
    '4  从本机检查 FPGA IP 网络是否可达' \
    '5  预览复位命令' \
    '6  预览 ROCm 与 NPU hybrid 运行命令' \
    '7  查看直连状态和展示顺序' \
    '8  FPGA 合成协议及屏幕演示（需板端已启动）' \
    'q  退出'
  printf '选择：'
  if ! IFS= read -r choice; then echo; exit 0; fi
  case "$choice" in
    1)
      echo "运行：bash $ROOT/npu_deploy/demo_amd_ai_pc.sh"
      if ! bash "$ROOT/npu_deploy/demo_amd_ai_pc.sh"; then echo 'AMD 演示未完成，请查看上面的错误。'; fi ;;
    2)
      echo "运行：$PY $ROOT/npu_deploy/direct_connection_inventory.py"
      if ! env -u LD_PRELOAD -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME -u GLIBC_TUNABLES \
          "$PY" "$ROOT/npu_deploy/direct_connection_inventory.py"; then
        echo '本机盘点未完成，请查看上面的错误。'
      fi ;;
    3)
      echo "运行：$PROJECT/run.sh cameras --diagnose"
      if ! env -u LD_PRELOAD -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME -u GLIBC_TUNABLES \
          "$PROJECT/run.sh" cameras --diagnose; then
        echo '相机诊断未完成；请检查设备、权限及上面的错误。'
      fi ;;
    4)
      printf '输入 FPGA 的 IPv4 地址（未知时按 Enter 返回）：'
      if ! IFS= read -r fpga_ip; then echo; exit 0; fi
      if [[ -z "$fpga_ip" ]]; then continue; fi
      if ! "$PY" -c 'import ipaddress,sys; a=ipaddress.ip_address(sys.argv[1]); sys.exit(0 if a.version==4 and str(a)==sys.argv[1] else 1)' "$fpga_ip" 2>/dev/null; then
        echo '请输入有效的 IPv4 地址。'; continue
      fi
      printf '运行：ping -c 2 -W 2 %q\n' "$fpga_ip"
      if ping -c 2 -W 2 "$fpga_ip"; then
        echo '该 IP 从 AI PC 可达；仅凭 ping 不能确认设备身份或 FPGA 已执行计算。'
      else
        echo '未收到该 IP 的 ping 回复；可能是链路、地址、设备或 ICMP 策略问题。'
      fi ;;
    5)
      echo "运行：$PROJECT/run.sh reset --check-only"
      "$PROJECT/run.sh" reset --check-only
      echo '以上只预览本机复位入口，不执行复位。' ;;
    6)
      echo "运行：$PROJECT/run.sh hybrid --backend rocm --check-only"
      "$PROJECT/run.sh" hybrid --backend rocm --check-only
      echo "运行：bash $ROOT/npu_deploy/run_hybrid_npu.sh --check-only"
      bash "$ROOT/npu_deploy/run_hybrid_npu.sh" --check-only
      echo '以上只预览本机 hybrid 入口，不启动机械臂。' ;;
    7)
      echo '建议演示顺序：1 AMD 硬件和模型 → 2/3 接口与相机 → 4 FPGA 网络 → 8 FPGA 合成协议。'
      echo '已有 FPGA RTL、板端服务和 AI PC 真实数据旁路代码；真实设备联动尚待验收。'
      echo '待现场确认：左右 CAN、三路相机绑定、实际带宽/权限及新版 FPGA 服务。'
      echo 'NPU hybrid 尚未在直连机械臂的完整实时控制中验证。' ;;
    8)
      printf '输入 FPGA IPv4 地址（未知时按 Enter 返回）：'
      if ! IFS= read -r fpga_ip; then echo; exit 0; fi
      if [[ -z "$fpga_ip" ]]; then continue; fi
      printf '运行：bash %q %q\n' "$ROOT/npu_deploy/demo_fpga_synthetic.sh" "$fpga_ip"
      if ! bash "$ROOT/npu_deploy/demo_fpga_synthetic.sh" "$fpga_ip"; then
        echo 'FPGA 合成演示未完成，请查看上面的错误和板端日志。'
      fi ;;
    q|Q) echo '退出。'; exit 0 ;;
    *) echo '请选择 1–8 或 q。' ;;
  esac
done
