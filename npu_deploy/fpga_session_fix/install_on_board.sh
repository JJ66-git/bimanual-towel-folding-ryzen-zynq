#!/bin/sh
set -eu
package_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
runtime_dir=${1:-/home/xilinx/jupyter_notebooks/fpga_offline_001/board_runtime}
if [ ! -d "$runtime_dir/board" ]; then
    echo "找不到板端运行目录：$runtime_dir" >&2
    exit 1
fi
if pgrep -f '[p]ython3.*-m board.runtime' >/dev/null 2>&1; then
    echo '板端服务仍在运行；请先在其终端按 Ctrl+C 并等待退出，再安装。' >&2
    exit 1
fi
for name in pynq_udp_server.py task_monitor.py; do
    if ! cmp -s "$runtime_dir/board/$name" "$package_dir/original/$name"; then
        echo "板端 $name 与预期原版不一致；未改动任何文件，请提供其内容以核对。" >&2
        exit 1
    fi
done
backup=$(mktemp -d "$runtime_dir/.session-fix-backup.XXXXXX")
for name in pynq_udp_server.py task_monitor.py; do
    cp -p "$runtime_dir/board/$name" "$backup/$name"
done
rollback() {
    for name in pynq_udp_server.py task_monitor.py; do
        cp -p "$backup/$name" "$runtime_dir/board/$name" || :
    done
    echo "安装失败，已尝试恢复原文件；备份：$backup" >&2
}
trap rollback HUP INT TERM
if ! cp "$package_dir/board/pynq_udp_server.py" "$runtime_dir/board/pynq_udp_server.py" ||
   ! cp "$package_dir/board/task_monitor.py" "$runtime_dir/board/task_monitor.py"; then
    rollback
    exit 1
fi
trap - HUP INT TERM
printf 'FPGA_SESSION_FIX_INSTALLED backup=%s\n' "$backup"
printf '%s\n' '请重新启动板端服务。FUI1/FPG1 诊断会话空闲 5 秒后可由新会话接替；不改变机械臂控制。'
