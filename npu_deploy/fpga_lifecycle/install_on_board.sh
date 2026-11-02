#!/bin/bash
set -euo pipefail
ROOT=/home/xilinx/jupyter_notebooks/fpga_offline_001/board_runtime
PACKAGE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ $(id -u) != 0 ]]; then echo '请用 root 运行此安装脚本。' >&2; exit 1; fi
if [[ ! -f "$ROOT/scripts/run_fpga.sh" ]]; then echo '没有找到原 FPGA 运行脚本。' >&2; exit 1; fi
if pgrep -f '[p]ython3.*-m board.runtime' >/dev/null; then
  echo '检测到板端 FPGA 运行服务。请先停止它，再运行安装。' >&2; exit 1
fi
if [[ -e /etc/systemd/system/fpga-lifecycle.service ]]; then
  echo '已存在同名 systemd 服务；为避免覆盖，请先检查该服务。' >&2; exit 1
fi
if [[ ! -f "$PACKAGE/control.key" || $(wc -c < "$PACKAGE/control.key") -lt 64 ]]; then
  echo '安装包缺少控制密钥。' >&2; exit 1
fi
install -d -m 700 "$ROOT/lifecycle"
install -m 600 "$PACKAGE/control.key" "$ROOT/lifecycle/control.key"
install -m 644 "$PACKAGE/board_manager.py" "$ROOT/lifecycle/board_manager.py"
install -m 644 "$PACKAGE/control_protocol.py" "$ROOT/lifecycle/control_protocol.py"
cat > /etc/systemd/system/fpga-lifecycle.service <<UNIT
[Unit]
Description=FPGA diagnostic display lifecycle manager
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $ROOT/lifecycle/board_manager.py
WorkingDirectory=$ROOT/lifecycle
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now fpga-lifecycle.service
systemctl is-active fpga-lifecycle.service
printf '%s\n' 'FPGA_LIFECYCLE_INSTALL_COMPLETE: 后台管理已启用；准备脚本将启动屏幕及 FPGA 诊断服务。'
