# 现有代码到 FPGA 的功能映射

以下路径均在 `source_snapshot/` 内。具体行号与 SHA256 见 `SOURCE_MANIFEST.json` 的 `function_locations`，以包内快照为准。

## 1. 第一批：确定性标量检查

| 功能 | 原文件/函数 | 输入 | 现有行为 | 本包参考的差别 |
| --- | --- | --- | --- | --- |
| 绝对限位 | `deployment_entry/blend_core.py::validate_absolute`；`cobot_motion_adapter.py::configure_bounds` | ACT/target 与 14 维上下限 | 使用现场包络、容差 1e-6；非法 ACT 关节不靠裁剪掩盖 | int32 微单位，绝对限位额外 1 单位，临界值可能因量化不同 |
| 每步角度变化 | `deployment_entry/cobot_hybrid.py::validate_execution` | target、上一最终 target | 十二个关节最大差≤1.0 rad | 相同比较结构，整数实现 |
| 跟踪偏差 | 同上 | measured、上一最终 target | 十二个关节最大差≤3.0 rad | 不是 measured-target，也不是速度；本包没有重新调紧阈值 |
| 修正幅值/变化率 | `blend_core.py::_apply_desired`；`cobot_motion_adapter.py::constrain_correction`；`validate_execution` | 当前/上一 correction | 幅值 0.15，变化率 0.02，执行验证带 1e-5 容差 | RTL 额外复查幅值不变量；变化阈值 20010 微 rad |
| 夹爪归属 | `cobot_hybrid.py::main`；`blend_core.py::apply_global_smoothing` | ACT、target、correction 的 6/13 维 | 最终夹爪直接取 ACT，Smol correction 夹爪为 0 | 显式生成不变量违规位，属于新增复查 |
| 起点容差 | `cobot_hybrid.py::main` | 实测位置、自有 task_start.target | 关节 0.12 rad、夹爪 0.012 m；execute 且未 resume 时检查 | 仅请求 flags.CHECK_START 才检查，不能在整段任务每拍当起点判断 |

动作索引：0…5 左关节、6 左爪、7…12 右关节、13 右爪。关节 rad→微 rad，夹爪 m→微 m。主机先拒绝非有限/维度错误/溢出输入，不能把 NaN 当零发送。

注意 `blend_core.py` 自带的 SINGLE_LOWER/UPPER 是历史基础边界。实际部署由 `configs/cobot_motion.json` 覆盖，J2 上限 2.54、J3 下限 -2.14；移植时不能直接抄旧常量。自己的起点也来自 task_start.json，不使用历史 ACT_START 常量。

## 2. 第二批：运行时序、复位和链路监视

`cobot_hybrid.py::main` 以 time.perf_counter 驱动 tick，并在不足 1/30 秒时补 sleep。ACT 原生队列可能连续消费 50 步，补块时推理耗时可超过名义周期；FPGA 可统计周期、超时和队列水位，但不能把每拍都收到观测误认为每拍都有新的模型视觉推理。

默认 step≥300 触发 Smol RTC resume；手动 handoff 文件也能触发，连续拒绝还可能取消本轮接管。因此硬件比较 step 只能给“交接已到期”提示。真正启用/暂停/失败状态要由主机明确传输；当前 v1 示例只报告名义 scheduled_phase，不实现完整状态机复制。

750 步预算由 for 循环限制，本包额外报告 step≥750。13:40 实录在第 667 条记录后回位限位停止，现场确认基本完成；16:06 的 750 条则是任务失败。两者都说明预算和计数不能充当任务成功判定。

`cobot_reset.py::move_slowly` 涉及另一组规则：关节 0.06 rad/s、夹爪 0.006 m/s，跟踪 0.12 rad/0.015 m，运动 corridor 限制，使能丢失直接停止，settle 最长 10 秒。适合移植到单独的 RESET 模式，但不能拿策略阶段的 1.0/3.0 阈值替代，也不能拿这组严格规则强加给策略运动。当前 motion_guard RTL 没有实现复位斜坡或使能检测，保留原源码给板卡开发。

`cobot_connection.py::connect_holding_measured` 用实测反馈预装目标并使能。原起点判断在连接保持之后，不能以策略尚未进入主循环断言从未下发命令。上板后也不能让“通信恢复”自动触发使能、清错、回零或重放旧目标。

新增链路监视包括 CRC、版本、配置标识、session、序号、回包匹配和独立 watchdog。当前反馈超时阈值为空；历史 trace 没有十二关节同源时间戳或逐 tick 完整队列水位，示例将它们设为 UNKNOWN。不能从 SDK 的 epoch 接收时间直接减去主机 monotonic 时间，也不能用“本次读取缓存的时刻”冒充数据更新时间。

## 3. 第三批：可研究的流式计算

- `policies/rtc/action_queue.py`：消费/合并动作、丢弃推理期间已过期前缀。可将水位和消费计数镜像到 FPGA；Torch 张量管理及任务文本标签仍由主机负责，不能让板端第二队列悄悄改变实际动作顺序。
- `policies/rtc/latency_tracker.py`：最大值、历史窗口和分位数。硬件容易做计数和最大值；分位数需要明确窗口与近似算法，本包不声称已实现。
- `blend_core.py::smol_trust/blend_step`：候选差、EMA、authority、修正约束。需要乘法器和清晰舍入/饱和规则；第一版只监视最终结果，保留浮点混合在主机，减少两端状态漂移。
- `towel_quality.py::cloth_features/update`：HSV 分割、形态学、轮廓、稳定窗口，可考虑后续硬件化像素统计。当前规则是特定蓝毛巾场景的可选启发式，近期启用模板的三轮均失败，当前基线不启用，不能硬化成成功判据。

## 4. 继续放在本机的部分

ACT/SmolVLA 推理、各自 processor/normalizer、文本 tokenization、相机训练通道适配、视觉语义判断和日志封存仍放在本机。现有 effort 常量训练适配也不能改成 FPGA 触觉信号。

本包只覆盖关节空间边界，没有标定后的自碰撞/桌面碰撞检查、逆运动学或有效抓持传感器。若以后做这些方向，需要另补几何、标定和传感器数据；目前不凭某个角度或耗时推断抓取/折叠完成。
