# 无机械臂、无相机阶段的完成记录

2026-11-14 在独立 `flexifold_working_baseline` 仓库的 `fpga-authority` 分支实施。
基线标签 `baseline-before-fpga-authority-20261114` 保留，原演示工作目录、模型权重、夹爪目标和运动边界不覆盖。
完整结果位于 `/home/ysyx/FPGA_competition/local_environment_record/fpga_offline_completion_20261114/`。

后续真实 GPU/NPU/B35 联合验收与双端显示已完成，见 [HARDWARE_ACCEPTANCE.md](HARDWARE_ACCEPTANCE.md)。

## 已执行的工作

| 工作 | 结果与适用范围 |
| --- | --- |
| 真实 CPU 模型 | ACT 输出 `[1,100,14]`；SmolVLA 普通/引导输出 `[1,50,14]`，有限值，引导梯度实际调用 10 次 |
| 独立编码器 | 独立进程加载实际 ONNX，由 CPU provider 执行；合成固定输入和历史真实图像均运行通过，不冒充 NPU |
| 原生/ONNX 数值对照 | 一组历史真实输入、普通与引导两种路径；最大动作绝对差均约 `7.75e-7`，逐维误差另存，不外推到全部输入或 NPU |
| 计算负载下本机协议 | 750 条真实 FoldClient/FoldSession 编解码检查全部与模型推理时间重叠，20 ms 窗口超时 0；内存传输，不是 PL/以太网测速 |
| 故障注入 | 19 类软件协议/授权故障通过：越界、步长、跟踪、夹爪所有权、修正变化、反馈过期/未知、截止时间、丢包、请求/回复坏 CRC、旧序号、错会话、错 build、参考失配、迟到、停止、下发回调异常 |
| 真实轨迹回放 | 2 轮成功与 4 轮失败，共 4417 条，整数参考结果与未修改的 `motion_guard.sv` 一致 |
| 原有 RTL 回归 | 8 组仿真通过，包括 AXI、LCD、阶段、视觉时序、撤臂过期和帧间隔回归 |
| 夹爪审计 | 六轮目标均保持 ACT 所有权，米到毫米单位转换核对一致；不将异步缓存误差当成逐命令物理跟踪误差 |
| 视觉离线原型 | 5 段机器人相机录像，1599 帧；1 段成功用于标定，4 段完整失败留出；失败录像均未触发目标几何候选，标定成功末段有 9 帧候选 |
| 回退工具 | 实际运行静态板端恢复计划、运行资产恢复计划；临时目录验证内容恢复、旧版本保留、符号链接、非硬链接复制、中途提交失败、传输失败、启动失败和篡改拒绝 |
| 生命周期 | 保留服务复用/同一设备锁；诊断中断走状态恢复，父进程给板端子进程清理时间，不直接跳过恢复；中断恢复路径有回归测试 |
| 授权热路径 | 新增只读电机反馈快照，保留原完整观测接口；刷新反馈不等相机，读锁等待有界，实际下发锁取得后再次核对同一绝对截止时间与停止请求 |

`repro/available_checks.json` 记录完整可复现入口执行结果。`cpu_models.json`、
`recorded_cpu_worker_load.json`、`recorded_numerics.json`、`cpu_joint_reference/acceptance.json`
是实际模型执行证据，不能只从包安装、provider 注册或配置字符串推断设备执行。
反馈与下发锁超时路径执行真实 wrapper/robot 方法，以假总线验证不读相机、不调用发送；
实际 SDK/CAN 耗时仍待现场测试。原连接、复位与保持退出流程不作安全能力扩张声明。

### 轨迹回放发现

四轮失败任务同样全部满足已有动作数值约束。这证明当前关节/修正规则不能识别布料是否折好，
不能将零 MASK、750 步结束或正常进程退出作为成功条件。

历史日志没有当前授权所需的完整单调反馈年龄与队列深度，因此回放保持 UNKNOWN，
不伪造年龄来取得许可。数值兼容通过与“当时可获授权”是两件分别验收的事。

### 图像、状态与视觉边界

`recorded_fixture.py` 从实际 `robot_events.jsonl` 提取完整 28 项 SDK 观测，按名字重排，
从三路录像选择该观测之前最近的缓存帧；本次缓存时间差约 84.59 ms。
原始文件 SHA-256、帧号和时间来源均保留。这不是原始策略张量捕获或同步曝光保证。

`visual_evidence.py` 要求匹配会话/事件、递增帧、新鲜时间、撤臂已确认和多帧稳定。
空桌、遮挡、未知撤臂、旧事件、重复/乱序帧及间隔过大都会清除或拒绝证据。
它最多输出 `MATCH_CANDIDATE`，从不认证隐藏折叠层数，也不向控制器发停止命令。

录像只有轮次级操作员标签，没有逐帧折叠/撤臂标注。11 月 2 日的成功日志存在，机器人相机录像缺失。
只有一段可用成功录像且已用于标定，不能报告独立成功召回率。
手机的部分完成视频属于另一相机视角，没有混入本次机器人相机标定。
回放为测试状态机使用明确标记的模拟事件身份和零传输年龄，不冒称历史 PL 事件或曝光新鲜度。
自动停止与现场视觉成功认证保持关闭。

## 可直接复现的入口

从普通终端运行，所有入口均不打开相机、不初始化 CAN、不复位或驱动机械臂。

```bash
cd /home/ysyx/FPGA_competition/flexifold_working_baseline
python3 tools/run_available_checks.py
```

默认执行本机回归、故障矩阵、历史动作/RTL/视觉回放、固定输入提取和只读恢复计划。
每轮用新日志目录，保留所有失败结果。

补做真实模型和独立 CPU 编码器检查：

```bash
python3 tools/run_available_checks.py --with-models
```

同时尝试 GPU/NPU/B35 联合负载与扩展实板故障注入：

```bash
python3 tools/run_available_checks.py --with-models --hardware-if-available
```

硬件入口先核对设备映射、网络与 NPU memlock，再加载实际模型；有条件时只在当前进程允许的硬限内提高软限，
不自动执行 sudo 或修改系统配置。模型成功完成推理后才启动板端诊断；
板端先备份、再同步文件，测试接收端仍为内存，最后恢复原来的服务状态。
进程存活、实际推理时间区间、GPU 执行以及 NPU 子图与首次有效输出分别记录。
固定输入联合负载报告仍保持 `full_controller_load_measured=false` 和 `eligible_for_policy_execution=false`。

只执行完整板端故障矩阵可用：

```bash
bash tools/run_fpga_benchmark.sh --fault-matrix
```

其中只有数值违规是在 PL 检出；错包、过期、会话与停止等由主机授权入口拒绝。
下发锁超时或停止请求在 SDK 调用前明确标记为未下发；进入 SDK 后的异常可能已经发生部分下发，
因此标记为“结果未知”，只验证不自动重试，不能声称收到零条物理命令。

## 回退操作

下面两条默认只读校验，当前已经执行通过：

```bash
python3 tools/restore_board.py --snapshot /home/ysyx/FPGA_competition/local_environment_record/rollback_board_20261114
python3 tools/restore_runtime.py --snapshot /home/ysyx/FPGA_competition/local_environment_record/rollback_runtime_20261114 --source-workspace /home/ysyx/FPGA_competition
```

确需恢复时，在结束设备操作后为对应命令添加 `--apply`。两者使用共享设备锁。
板端恢复校验原快照的 62 个静态文件；不回写旧 PID、动态 LCD/运行状态。
恢复前另存当前实板快照，传输或启动失败则恢复先前字节与来源/显示模式。
运行资产恢复先完整复制并逐文件校验，所有角色就绪后才替换目标；原目录保留为 `.pre_restore_*`。
`--role` 可选择模型或环境单项恢复；不会运行 pip 或更改系统驱动。

Git 源码取回仍使用基线标签的新 worktree，参见 [PHASE1.md](PHASE1.md)。
本轮没有实际回退正在使用的环境或板端服务，也没有重烧位流。

## 尚不能完成的验收

本会话连接 `192.168.2.99:9090` 返回 `PermissionError: Operation not permitted`，
也未映射 `/dev/kfd`、`/dev/dri`、`/dev/accel`。相应实板扩展注入、GPU/NPU 联合测速与
板端显示文案同步均记录为未执行，不能用 CPU 或仿真结果顶替。
上一阶段实际 B35 的 750 次空载往返验收仍有效，但不自动覆盖本阶段增加的测试。

设备接回后还需真实反馈/相机负载、拒绝后保持退出、夹爪物理开合、独立成功录像留出、
遮挡/掉落/空桌等视觉负例，以及任务成功率验收。
USB-CAN 仍由 AI PC 直连；独立硬件 CAN 网关/PC 卡死时的制动能力没有新增或宣称实现。
