# 无机械臂、无相机的真实硬件验收

2026-11-14 在 `flexifold_working_baseline` 的 `fpga-authority` 分支完成。
外部证据目录：`/home/ysyx/FPGA_competition/local_environment_record/fpga_hardware_completion_20261114/`。
所有执行器接收端均为内存；未连接相机、初始化 CAN、复位或下发真实机械臂动作。

## 结果

| 项目 | 实测 |
| --- | --- |
| AI PC | Ryzen AI HX 370 / Radeon 890M；ROCm 7.14，XDNA NPU 可访问 |
| 模型 | ACT 50k、SmolVLA 30k 原权重；真实 GPU 执行、NPU 实际 kernel 执行证据均存在 |
| 联合负载 | 140 轮固定历史图像模型循环；750/750 板端样本与真实推理区间重叠 |
| PL 往返 | 750/750 匹配；20 ms 窗口超时 0；P50 2.700、P95 3.867、P99 8.005、最大 16.730 ms |
| 显示回执负载 | FAS1 发送 248、匹配确认 248；策略线程无显示 socket 等待 |
| 实板故障矩阵 | 19 类通过；数值违规由 PL 检出，错包/过期/停止等由主机入口拒绝 |
| 双端联动 | 8 个实际 PL/内存接收端场景；当前会话、序号、判定与回执绑定 |
| 浏览器 | 1520、1024、640 宽度实板只读 feed；无脚本错误或横向溢出 |
| AI PC 原生界面 | 模拟子程序走完原有阶段确认流程；窗口回调错误 0；无设备动作 |
| 回归 | 170 项 NPU/主机测试、37 项 FPGA 主机/显示/协议测试、23 项演示测试通过 |
| 实板恢复 | 同 PID 复用、取消延迟停服、实际文件恢复、真实写入后丢确认的精确回滚、中断诊断后恢复原服务模式均通过 |
| 原始工作区 | 1052 个文件内容及 4 个链接保持原基线；36 个运行依赖身份匹配 |
| 最终状态 | LCD 控制寄存器为 0；背光关闭；UDP 服务保持 host_observation、bulk_vectors，lcd=false；FPG1 与 FUI1 探针均通过 |

## 显示与状态含义

沿用现有 AI PC 和 LCD 配色、字体、布局与同窗入口。共用 `host/action_status.py` 的文案和状态投影，
使用独立 FAS1 显示通道；它不签发动作许可、不延长 FPG1 控制租约、不写运动寄存器。
候选/送检/等待回执/核准通过/主机已下发/拒绝/下发未知/退出/结束分别显示。
有效核准只对应本条目标；显示超过期限会退回历史状态，不继续显示当前有效许可。
拒绝和下发未知在同一会话中锁定；旧 PASS 不会覆盖当前故障，新会话清除旧状态。

AI PC 协同页给出模式、接收端、会话、动作序号、反馈年龄、往返、窗口和下发回执；
实时画面页与运行引导也显示关键核准状态，不强迫切换页面才能发现拦截。
退出码 0 不能覆盖本轮核准故障。匹配的被拒绝候选也进入诊断历史，超时不能借用旧回包。

LCD 突出本条结论和原因，保留阶段与视觉证据区。字体子集已补齐新增汉字并做 cmap 覆盖检查。
内存接收始终标明合成/诊断来源，不能认证折叠成功，主机回执也不能认证机械臂物理执行。
独立显示进程可合并中间画面，控制判定和错误记录不被丢弃。

## 证据与复现

- `joint/acceptance.json` 以及其引用的模型和板端逐条报告：真实联合负载、NPU profiler、19 类故障、原服务恢复。
- `dual_display_final/display_acceptance.json`：等待、核准中、内存接收、PL 拒绝、结束、丢回包、下发未知、新会话等待；另保存双方截图和 JSON。
- `browser/browser.json`：实板只读 feed 的浏览器检查；`native_panel/verification.json`：AI PC 原生模拟流程。
- `recovery_final/recovery.json`：实板恢复与故障注入；各次恢复前保留完整板端快照。
- `board_clock_sync.json`：板端原时钟明显落后，已同步并在新 Jupyter 会话验证；所有协议截止时间仍使用单调时钟。
- `lcd_closed_final.json`：LCD 寄存器、服务状态与探针结果；重复关闭复用同 PID。
- `hardware_recorded_20261114.json/.npz`：NPU 路径数值对照；`hardware_recorded_rocm_cpu_20261114.json/.npz`：CPU 编码器对照。
- `verification.json` 汇总本轮证据、文件哈希与明确未验收范围。

在工作仓库使用现有项目 Python，不修改系统安装。根目录新管理工具缺少 WebSocket 依赖时自动切到现有项目 Python。

```bash
P=bimanual-towel-folding-ryzen-zynq
PY=$P/amd_hybrid_deploy_20261031/.venv/bin/python
$PY $P/npu_deploy/fpga_authority/host_acceptance.py --execute --fixture /path/to/recorded_fixture/observation.json --out /tmp/new_joint
$PY $P/npu_deploy/fpga_authority/display_acceptance.py --out /tmp/new_display
python3 tools/verify_board_recovery.py --out /tmp/new_recovery
python3 tools/close_board_display.py --out /tmp/lcd_closed.json
```

所有输出应选新目录保留失败记录。前两个验收入口备份/同步后恢复原服务状态；最后关闭显示时，
刻意保留无 LCD 的服务运行。上述验收使用共享设备锁，不能与真实任务或互动并行抢占设备。

## 适用边界与保留的问题

固定历史图像不是当前真实相机，未测完整控制入口的相机、反馈、SDK、CAN 和机器人运动负载。
所有报告保持 `eligible_for_policy_execution=false`、`full_controller_load_measured=false`；不产生真机资格报告。
帧缓冲页面翻转的硬件 generation/page 与 PNG 记录一致，但 B35 VRAM 是写入接口，读操作为 AXI SLVERR；
本次没有声称读回像素或目视认证实体屏幕。首次尝试 VRAM 读回失败记录保留在 `dual_display/`，
随后按 RTL 能力改为校验实际页面翻转寄存器；不修改位流。

一组历史输入下，SmolVLA 普通/引导 NPU 路径相对 GPU 原生输出的最大差异分别为 0.034014/0.034285 rad，
CPU ONNX 编码器相对 GPU 原生输出约为 0.002757/0.002783 rad。NPU 的夹爪维差异不超过约 0.000196 m。
这不是数值等价验收，也没有足够依据认证现场任务质量。原模型、NPU 选项、融合和夹爪策略不因本次诊断改动；
实际夹爪目标仍由 ACT 提供。逐维数组和真实执行日志已保留，供连接设备后的完整验证使用。

成功/失败历史动作都可能满足关节规则，因此零违规、步数结束和进程正常退出都不代表折叠成功。
自动成功判断继续关闭，现场视觉标定、真实撤臂事件、夹爪开合和完整受控退出仍需连接设备验收。
原 USB-CAN 由 PC 直连，B35 的运动反馈时效检查未启用；主机有额外年龄检查，不能当成 PL 独立物理制动。

基线标签、原工作区、权重和运动边界保持不动；本次板端修改前均有完整快照。
