# B35 board integration

2026-11-13：当前选择 FPG1 v3 / FVE1 v1，板端 B35，BUILD_ID `0x46504735`。已通过 Jupyter 实际部署、PL 合成测试和主机 UDP 验证。视觉配置尚未现场标定，当前为阶段观测模式；详细问题及证据见 [更新报告](../../docs/FPGA_B35_RELEASE_zh.md)。

新版动态 LCD 与电脑协作页面见 [协作界面说明](../../docs/FPGA_COOPERATIVE_VISUAL_zh.md)。电脑打开 `bash npu_deploy/fpga_board/open_dashboard.sh`，入口为 <http://127.0.0.1:8765>；图像与任务结论来自真实板端快照。

## Entry points

| Path | Purpose |
| --- | --- |
| `active_profile.json` | 当前协议、板卡地址、时钟与现场验证状态 |
| `notebooks/02_inspect_and_validate_b35.ipynb` | 用户可审阅的检查、部署、回滚与合成测试入口 |
| `jupyter_client.py` | 本机密码窗口、私有会话、Notebook REST/kernel 访问 |
| `control_jupyter.py` | 仅管理此版本的 start/status/finish/stop |
| `build_release.py` | 生成不含凭据的发布 ZIP、文件 SHA256 清单和 B32 回滚材料 |
| `validate_board.py` / `check_udp.py` | 真实 PL 子集测试 / 主机真实 UDP 合成测试；不访问机器人 |
| `../artifacts/fpga_board/` | 生成的发布包与私有会话；不入 Git |

板端根目录为 `/home/xilinx/jupyter_notebooks/fpga_fpg1_v3_b35_20261113`。原 B32 项目和旧生命周期管理器保留。板端运行 Python 3.8 / PYNQ 2.7，PC 工具使用主项目 Python 3.12 环境。

Notebook 网页入口：[Open board Notebook](http://192.168.2.99:9090/notebooks/fpga_b35_handoff_20261113/02_inspect_and_validate_b35.ipynb)。默认烧录、PL 合成测试、LCD 测试与服务启动开关均为 False。服务运行时持有 PL 锁；手动重跑烧录或测试前，先停止本版本服务，避免多个进程同时写寄存器。

## Commands

在项目根目录执行。已有会话可直接使用，失效后登录工具会弹出本机密码窗口。密码不写日志，私有会话目录权限 0700，cookie 文件权限 0600。

```bash
amd_hybrid_deploy_20261031/.venv/bin/python npu_deploy/fpga_board/jupyter_client.py --login
amd_hybrid_deploy_20261031/.venv/bin/python npu_deploy/fpga_board/control_jupyter.py status
amd_hybrid_deploy_20261031/.venv/bin/python npu_deploy/fpga_board/control_jupyter.py start
bash npu_deploy/check_before_hardware.sh --software-only --probe-fpga
```

`start` 使用 `host_observation` 并启用 LCD。`finish` 延时十秒停止；期间再次 `start` 会取消停止。`stop` 立即停止本版本的已核验 PID。使用不同来源启动前须先 stop；不会把测试服务直接当成正常观测服务使用。

准备/运行界面仍使用 `prepare_showcase.sh --panel`、`start_showcase.sh --panel`。当前模式从 active_profile 读取：B35 使用 `AIPC_FPGA_FOLD`；旧版 B32 使用 `AIPC_FPGA_SHADOW`，两者不可同时开启。通用地址变量为 `AIPC_FPGA_ADDRESS`。没有自动降级或跨协议回包兼容。

仅接板卡时可以执行上述只读预检；正式机器人入口仍需三路相机、两臂 CAN、身份核验与操作员分阶段确认。当前不具备完整现场验收条件。

## Clock and evidence

B35 已重新构建，B34 原始 bit/HWH 保留回退；FCLK 分频为 5×4。PYNQ 2.7 默认的 50MHz PS 参考频率与本板设备树的 33.333333MHz 不同；`board/clock_reference.py` 先校准软件参考值，确认实际 FCLK0 为 50MHz。PL 自主 1.5 秒超时实测约 1.506 秒，不能只看未校准的 PYNQ 读数。

本版本不喂 FWD1 watchdog，不发送 CAN 动作。LCD 显示来源、阶段、视觉状态、稳定帧数和数据新鲜度，并保留标定未完成标记；帧缓冲/扫描计数已实测，用户已完成实体屏编号1989的目视确认。板端日期已同步并设置Asia/Shanghai时区。

维护入口：`lcd_visual_check.py --out <record.json>` 显示带编号的实体检查画面，会暂时停止本版本刷新；目视后用 `control_jupyter.py start` 恢复。`sync_clock.py --out <record.json>` 按本机时间手动校准板卡；必要时弹本机登录窗口。普通 `check_before_hardware.sh --probe-fpga` 仅检查，不修改日期。
