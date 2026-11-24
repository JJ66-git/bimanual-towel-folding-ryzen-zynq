# FPGA B34 integration report

> 历史记录：本页描述 B34 初次整合。当前已发布 B35 并落实本文的 RTL/配置 CRC 修订，请看 [B35 release](FPGA_B35_RELEASE_zh.md)。

日期：2026-11-13。输入为工作区 `handoff_fpg1_v3_pc_20261113/`。原交接包 44 个清单项最终 SHA256 复核全部通过。已集成主机与 PS 程序并在连接的 Zynq-7020 上验证 B34；当前可以做协议、PL 和显示联调，尚未完成真机现场验收。全程未使能、复位或驱动机械臂。

## Organization

原始交接目录和 ZIP 保留不动。运行代码进入既有 `amd_hybrid_deploy_20261031/`、`fpga_integration/fpga_code/fpga_workspace/`，没有另建一套竞争的主项目。新增入口见 [board README](../npu_deploy/fpga_board/README_zh.md)，新增目录和文件名使用英文。

修改前备份与验证证据集中在工作区 `local_environment_record/fpg1_v3_update_20261113/`，其中 `pre_update/` 保存被覆盖文件，`git_status_before.txt` 区分已有修改。原有 UI 与模型部署工作已保留。旧 B32 standalone runtime、板端项目和旧管理器也保留；版本切换必须明确选择，不自动降级。

板端新目录为 `/home/xilinx/jupyter_notebooks/fpga_fpg1_v3_b34_20261113`。最终发布 55 个文件全部在板端重新计算 SHA256，零差异。管理通过已认证的 Jupyter，会话存储在本机受限目录；没有迁移板端旧生命周期密钥。Notebook 已上传到板卡网页并保留本地副本。

## Verified interfaces

| Item | Actual result |
| --- | --- |
| PC Ethernet | `enp195s0`，MAC `68:1d:ef:59:34:43`，`192.168.2.10/24` |
| Network profile | `fpga-zynq7020`，自动连接、非默认路由；Wi-Fi 默认路由保留 |
| Zynq Ethernet | `eth0`，MAC `00:0a:35:00:22:01`，`192.168.2.99/24` |
| Reachability | ping 3/3，平均约 0.23ms；SSH 22 可达，实际管理使用 Jupyter 9090 |
| Board runtime | PYNQ 2.7.0，Python 3.8.2，Jupyter kernel 为 root |
| PL guard | `0x43c00000`，范围 64KiB；BUILD_ID `0x46504734` |
| LCD IP | `0x44000000`，范围 1MiB；BUILD_ID `0x4c434431`，panel ID `0x4384` |
| Clock | PS 参考 33,333,333Hz；IO PLL 30 倍、FCLK 分频 5×4，实际 50MHz |
| UDP | 9009，FPG1 v3 / FVE1 v1；FUI1 状态应答单独验证 |
| Final source | `host_observation`，LCD 已启用，合成状态清空，等待新样本 |
| Cameras / CAN | PC 当前未枚举到 `/dev/video*` 或两臂 SocketCAN，身份与真机反馈尚不能核验 |

原始位流 SHA256：`4b8cafe8e742b78ba1448eb1ab29e7f781cd3edc18c8cc8d1b6c5f654e0d8d12`。
原始 HWH SHA256：`6cad3f1f2ab7dbe5ecf8411e8f08b75c4a31968021706087f28c58aa480d72d7`。

时钟排查中曾尝试 6×5 分频，实际降低到约 33.33MHz，因此已撤销并归档到 `clock_trial_6x5/`。最终没有修改原始 bit/HWH。根因是 PYNQ 默认参考频率不符合本板设备树；当前校准软件参考值，且用 PL 自主超时实测验证。不得使用那次试验文件作为发布材料。

另行只读检查发现板端系统日期停留在 2021 年，与主机不同，见 `board_wall_clock_inventory.json`。协议反馈年龄、图像时间及超时均使用 monotonic 时间，不依赖此日期。板端旧日志文件名不能当作实际测试日期，归档以 PC 审计记录为准；系统日期同步与重新认证应在后续板卡维护时一起处理，本轮未改动系统日期。

## Changes

主机使用冻结的 v3 报文布局，严格检查 magic/version/kind、CRC、长度、会话、序号与时间戳；控制和视觉双 socket 保持各自身份，支持视觉重试与终态查询。修复新会话不清步数/时间、阶段标志检查不完整、任务启动线程竞态及视觉只发首帧等问题。

SDK 被动接收回调为关节三组 CAN 报文和夹爪分别记录 monotonic 时间。读取与更新使用同一锁，以最旧有效分量计算反馈年龄，缺失则 UNKNOWN；队列等待时间也计入年龄。原有发送接口与动作限制保留。相机生产者将帧、帧号和就绪时间一起缓存，不另开摄像头读取线程冒充新帧。

板端增加独占 PL 所有权、版本范围内的 Jupyter 生命周期管理、FUI1 状态应答及 LCD 缓存显示。已验证十秒延时停止、再次启动取消停止、停止后重新启动；旧 B32 管理进程仍存活。FPG1/FVE1 不喂 watchdog。

PC 界面显示 B34 阶段、视觉状态、稳定帧数、BUILD_ID 和反馈年龄；标记历史数据与标定状态。宽屏、紧凑截图均已检查：[wide](assets/dashboard_b34_simulation.png)、[compact](assets/dashboard_b34_compact.png)。截图使用模拟数据，不能当成现场成功画面。

## Verification evidence

下列文件均位于 `local_environment_record/fpg1_v3_update_20261113/`：

| Record | Scope and result |
| --- | --- |
| `board_target_validation_result.json` | 真实 PL、合成输入；探针、OPEN/GRIP/FOLD、撤臂事件、三帧稳定计数 1/2/3、终态查询、新会话复位、自主超时、watchdog 未使能，10 项子集检查通过 |
| `board_validation_summary.json` | 实板 BUILD_ID、FCLK 50MHz、LCD 身份；自主超时约 1.506113 秒 |
| `pc_udp_validation.json` | 真实 PC→PS→PL UDP；17 个任务样本、三帧结果、查询恢复、坏 CRC/截断/旧协议拒绝、新会话复位通过；RTT 中位 4.157ms、最大 4.683ms |
| `lcd_interface_result.json` | 实际帧缓冲提交 generation=1，约 0.1 秒扫描计数增加 8；未宣称物理屏目视通过 |
| `final_board_inventory.json` | 最终 55 文件哈希一致；HOST 来源、零任务样本；旧管理器保留 |
| `lcd_host_observation.png` | 板端实际生成的最终 LCD 帧缓冲预览，已检查排版；不代替目视物理屏 |
| `service_finish_cancel.json` / `service_finish_stop_restart.json` | 真实 Jupyter 管理：取消延时停止、自动停止及重新启动 |
| `project_regression.log` | 68 项主项目回归通过 |
| `handoff_pc_tests.log` | 交接附带 8 项 PC 测试通过 |
| `lifecycle_regression.log` | 旧 B32 生命周期 6 项回归通过 |
| `rtl_simulation/summary.json` | 原工程两个单元测试通过；两个新缺陷用例在原 RTL 如预期失败，在待修 RTL 通过 |

旧工作区全量测试另有缺失原始 fixtures / PYNQ mock 引起的失败，见 `fpga_workspace_regression.log`；不能把上面的通过项解释为工程师全部验收项目通过。PL 合成测试也不代表摄像头标定、真实 CAN 时序、任务成功率或规范全量符合性。

## Remaining FPGA work

以下修订集中在 [pending_rtl_fixes](../fpga_integration/fpga_code/fpga_workspace/pending_rtl_fixes/README.md)。活动 `rtl/` 保持与原始位流一致，补丁未应用到当前 FPGA。本机没有 Vivado，尚不能生成新位流。

1. `fold_stage` 反馈断流计数饱和在 200ms 阈值，而有效性使用 `<=`，导致永不过期。待修版本允许计数到阈值+1，新增回归已证明修复。
2. `fold_vision` 帧间隔错误地相对稳定首帧计算。三帧各间隔 250ms 时第三帧会被重置；待修版本相对前一帧计算，新增回归通过。
3. `axi_lite_guard` 视觉结果拼接少四位，当前 bit 的 stable_count 实际位于 `[19:12]`。活动 PS 已按实板解码，真实三帧验证通过；待修 RTL 补足位宽后将位于 `[23:16]`，必须同时调整 PS 解码，更新 BUILD_ID 并重新验收，不能单换 bit。
4. phase config CRC 未实现、视觉形态/暗区门限未现场标定。还需 FPGA 工程师补齐配置身份及匹配发布材料，并用真实三路相机、两臂反馈完成现场验收。

当前 `quality_profile=null`，主机只观测阶段，不发送未经标定的 LIVE 视觉成功证据。测试来源始终标记 SYNTHETIC，不能混入正常观测。最终只读预检为 `LOCAL_SOFTWARE_READY=true`、FPGA 诊断通过、`HARDWARE_READY=false`、`READY_FOR_MOTION=false`；完整结果见 `npu_deploy/artifacts/readiness/latest.json`。

AMD CPU/ROCm/NPU 配置、已部署 ACT/SmolVLA 权重与前期性能验证保留。此次 FPGA 改动没有重训或改写模型参数。
