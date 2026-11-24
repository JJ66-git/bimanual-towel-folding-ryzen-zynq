# FPGA B35 revision release

2026-11-13：FPGA 修订已在本机重新构建并完成 Zynq-7020 实板验收，当前选择 B35，BUILD_ID `0x46504735`。本发布资格为 **SYNTHETIC 修订联调版**，真实摄像头标定与机械臂闭环验收另行完成。没有发送机械臂使能、复位或 CAN 动作。

## Work completed

1. 修复 `fold_stage` 的 200ms 断流计数饱和，超过阈值后撤臂有效标志失效。
2. 修复 `fold_vision` 的相邻帧间隔计算，保持面积/中心对稳定首帧锚定。
3. 修复 AXI 视觉快照拼接为完整32位，同步 PS 解码：count=[23:16]、reason=[15:8]、done=bit31。
4. 新增完整阶段/视觉配置 JSON，按照冻结规范生成唯一字节串、CRC32、SHA256 和 RTL 常量；真实门限直接引用生成常量，0x098 返回该 CRC。PS 启动核对配置资格、guard 配置身份、BUILD_ID 和 PL CRC；PC 预检比对两端配置 SHA 与启动校验值。
5. 使用新 BUILD_ID 区分原 B34，保持 FPG1 v3 / FVE1 v1 线上布局不变。当前 HOST 来源拒绝有效视觉特征，避免未标定阈值产生现场成功结论。
6. 离线安装 Vivado 2018.3 WebPACK 与 Zynq-7000 支持，完成综合、布局布线、位流生成、独立实现检查、实板回归及 PC UDP 回归。

## Release and rebuild

| Item | Location / identity |
| --- | --- |
| Active project | `fpga_integration/fpga_code/fpga_workspace/`，主机/PS/RTL/配置已同步 B35 |
| Source and build snapshot | `fpga_integration/release_candidates/fpg1_v3_b35_20261113/`，此目录已验收，含源码、重建脚本、报告及 acceptance 摘要 |
| Final ZIP | `npu_deploy/artifacts/fpga_board/releases/b35_pc_update.zip`，94个文件，逐文件 SHA256 清单；不含凭据 |
| ZIP checksum | 同目录 `b35_SHA256SUMS` |
| Review Notebook | `npu_deploy/fpga_board/notebooks/02_inspect_and_validate_b35.ipynb` |
| Board runtime | `/home/xilinx/jupyter_notebooks/fpga_fpg1_v3_b35_20261113` |
| Active selection | `npu_deploy/fpga_board/active_profile.json` |
| Toolchain | `/home/ysyx/FPGA_competition/tools/xilinx/Vivado/2018.3`；目标 `xc7z020clg400-2` |

可通过 [board Notebook](http://192.168.2.99:9090/notebooks/fpga_b35_handoff_20261113/02_inspect_and_validate_b35.ipynb) 审阅；默认烧录、测试与启动开关均为 False。重建命令见 [source README](../fpga_integration/release_candidates/fpg1_v3_b35_20261113/README_zh.md)。所有构建必须使用新输出目录，不覆盖既有证据。

bit SHA256：`ec0a06b7994cfc1447779dad92c133d056735a0e9ae1acdb756cfa49bc3ce33d`。

HWH SHA256：`96b0dc4ea4c7b2b2c858af8223796f3131aa49873dc6c2a08d0fb1eedfec2a67`。

配置规范字节 SHA256：`f3c948cd21b52e40421c1e56b9ea078e91731034e9ab347c708dffb10ac45169`；CRC32 十进制 `1701428268`，与板端 0x098 匹配。

## Validation

证据集中在工作区 `local_environment_record/fpga_revision_release_20261113/`：

| Check | Result |
| --- | --- |
| RTL | 六个原有测试及两个缺陷回归，8项全部通过；原 guard 的1678个向量通过 |
| Configuration | 规范化顺序、浮点/重复键拒绝、身份校验、旧 BUILD_ID 拒绝、快照布局等8项通过 |
| Project regression | 68项通过，保留已有模型、启动界面和观察功能 |
| Routed implementation | setup裕量4.735ns，hold裕量0.057ns；DRC无Error，保留24条LCD输入/流水线相关Warning |
| Physical constraints | 24个RGB三态输出、LCD复位L17、像素周期40ns检查通过 |
| Real board identity | B35，实际FCLK0 50MHz，配置CRC一致 |
| Real PL base cases | 原阶段、撤臂、三帧、终态查询、新会话复位、超时及watchdog不使能通过 |
| Joint expiry regression | 停止输入230ms后返回 `[frame=2,state=CHECKING,reason=RETREAT_LOST,count=0]` |
| Frame gap regression | 持续刷新关节条件下，相邻250ms三帧返回count=1/2/3，最终OBSERVED，原B34在此用例失败 |
| Autonomous timeout | 约1.506291秒，来源SYNTHETIC |
| Real PC UDP | 双socket、三帧结果、终态查询、旧协议/截断/坏CRC拒绝、新会话复位通过；RTT中位4.703ms，最大5.993ms |
| LCD | 帧缓冲提交及扫描计数通过；约0.1秒增加9帧，不代表物理屏目视验收 |

主要记录：`release_acceptance.json`、`board_acceptance.json`、`pc_udp_validation.json`、`rtl_final/summary.json`、`config_tests.log`、`project_regression.log` 和最终 readiness 记录。它们是本次修订范围内的验收，不能替代完整现场规范验收或视觉准确率报告。

## Rollback and remaining field work

旧 B34 板端目录、原始 bit/HWH 和交接包保留。最终 ZIP 内 `rollback/` 包含 B34 bit/HWH；切换前源码与 PC 配置保存在本轮证据的 `pre_activation/`。回退须同时切换 PL、PS 和 PC 版本，不能只换位流。Jupyter 管理按 active_profile 选择目录，并以独立进程隔离 Notebook 缓存模块，避免跨版本混用。

当前配置 `calibration_status=synthetic_test`，特征算法为明确的合成向量输入，真实图像 HSV、形态参数及现场标注尚未交付。HOST 模式保持阶段观测、关闭有效视觉证据；不会把合成配置改名为现场配置。下一阶段需接相机、标定实际配置及特征提取，并重新生成匹配位流和验收；这是现场标定阶段，不是此次修订遗漏。

机械臂CAN、三路相机身份、视觉标定与闭环任务仍待现场测试。LCD实体目视和板端日期同步已在后续维护完成，见下节。协议年龄和超时继续使用monotonic时基，当前 `READY_FOR_MOTION=false`。

## LCD and date maintenance

2026-11-13 后续维护已完成：用户查看实体LCD的 `LCD CHECK / TEST 1989` 图案，确认画面完整、方向和红绿蓝白颜色正常、无明显闪烁。人工确认记录与检查帧在工作区 `local_environment_record/board_maintenance_20261113/`，板端同样保存于 `maintenance/`。物理确认与帧缓冲驱动的自动自检分开记录；自动自检的 `physical_display_verified=false` 表示它不能自行作出人眼结论，不否认本轮人工证据。

系统壁钟已从2021年校正到主机当前日期，板端时区为 `Asia/Shanghai`。时钟跳变使旧Jupyter登录过期后，已通过本机密码窗口重新认证，并在新会话读回验证。正常B35观测及LCD状态画面已恢复，新运行日志使用正确日期。

就绪检查现在同时比对实际板端日期与主机时间，以及人工LCD证据的BUILD_ID、面板ID和板卡MAC。这是本轮手动校时，未宣称NTP自动同步或断电后时间持久化；重启后再次运行只读预检即可检查实际状态。未改动发布位流、PL参数或机械臂动作链路。
