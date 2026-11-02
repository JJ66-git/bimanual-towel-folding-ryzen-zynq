# Verified PYNQ runtime

2026-11-01。启明星 7020 / B32，PYNQ 2.7。包内 HWH 已适配当前已验证的 PYNQ 启动配置：PS 参考约 33.333333 MHz，IO PLL FBDIV=30，FCLK0 分频 5×4，名义约 50 MHz。请同时使用包内 .bit 与 .hwh，保留原始交付。

本次实际通过：46 项软件测试；实体板 PERIODIC/ON_RESULT；667 帧 FPG1；watchdog smoke；1,678 组 PL 向量。位流没有重新综合；HWH 是运行时适配版本。

在 Jupyter 根目录上传 pynq_udp_runtime.zip，解压到一个新的目录，例如 flexiguard_verified。在 Jupyter Terminal 内进入该目录，确认没有另一个 UDP/MMIO 服务，再执行：

```bash
mkdir -p validation_runs/new_case
/usr/local/share/pynq-venv/bin/python3 -B -u -m board.pynq_udp_server --bitfile overlay/own_hybrid_motion_guard_watchdog.bit --bind 0.0.0.0 --port 9009 --peer 192.168.2.10 --log validation_runs/new_case/board.jsonl
```

--log 必须为新文件。每次新的 PC 会话前，正常停止并重启服务。FPG1 不刷新 watchdog；FWD1 的显式观测事件按模式处理。输入与结果只用于诊断，不连接机器人动作链。

更换 PYNQ 镜像/FSBL 或 PS 启动配置后，必须重新验证实际 IO PLL 和 PL 计数速率；此包不通用于任意 Zynq 板。当前板上的 PYNQ Clocks.fclk0_mhz 因 SDK 默认 50 MHz 参考而显示 75 MHz，不用它作真实频率依据。本次以计数/板端时间校准。

需要独立板测时，先停止服务，再运行 board/pynq_watchdog_smoke.py、board/pynq_overlay_vectors.py；后者的 board/vectors.txt 已包含。完整协议和数据导出见工作区 docs/WATCHDOG_STREAM.md。
