# FPGA 动作授权第一阶段

开发位置：独立仓库 `flexifold_working_baseline`，分支 `fpga-authority`。
原演示工作目录没有被本次开发覆盖，基线标签仍为
`baseline-before-fpga-authority-20261114`（`7b537e9614a0b78d5145cbd13268f7aa1bb3b881`）。

## 已实现的控制关系

```text
AI PC：ACT / SmolVLA 候选 → 原 CPU 校验 → 刷新编码器反馈、再次校验
                                         ↓ 本条目标/反馈/配置/会话/序号
FPGA PL：B35 关节、步长、修正等约束检查 → 匹配回包
                                         ↓
AI PC：核对身份、参考结果、时限、反馈年龄、停止请求
       ALLOW → 原目标仅下发一次
       DENY / 超时 / 错包 → 本实例锁定 → 原控制入口受控退出
```

`cobot_hybrid.py` 的策略下发分支已实际调用 `dispatch_policy_target()`。
选择授权模式后，不再启动另一个事后 observer；拒绝不能退回旧下发路径。
候选被固定并与送检载荷绑定，不能借用上一条 PASS；不自动重发执行器命令。
退出沿用原来的“读取当前测量姿态并保持”流程，不发送被拒绝的候选。
连接时保持、人工复位、退出保持仍属原生控制流程，不能宣称全部 CAN 帧都经过 FPGA。

这是软件执行入口依赖真实 PL 判定的机制。现有 USB-CAN 仍由 AI PC 直连机械臂，
没有新增物理 CAN 网关，也没有证明 PC 卡死时可独立制动。
B35 运动检查的 AGE_CHECK_ENABLED 仍为 0；授权器在主机端额外检查反馈年龄，不能称为 PL 独立看门狗。

ACT 50k、SmolVLA 30k、夹爪目标、模型权重、运动边界和 B35 位流不改动。
本阶段不启用真实视觉自动成功判断；步数结束、零违规、人工确认仍不能代替布料折叠结果。

## 时序优化及边界

- `--bulk-vectors`：六组 14 维整数使用 MMIO 数组写入，保留寄存器空隙、符号位和最后提交顺序。
  将原 84 次 Python 标量写改成 6 次数组赋值；加速幅度以实板往返数据为准。
- `--isolated-lcd`：独立进程渲染/写屏，仅映射现有 LCD 地址，不另行加载 Overlay。
  主 UDP 进程用有界非阻塞管道发送快照；可合并中间画面，完整计数和终态仍保留。
  渲染进程异步初始化，UDP 不等待首帧。`service_health.json` 和 `lcd_worker_startup.json`
  分别记录监听/显示就绪与渲染启动阶段；验收另核对当前渲染进程、匹配 PL 回包和 PNG 哈希。
- 相机、推理在刷新反馈之前。暂定 20 ms 是“刷新反馈到开始下发”的窗口，
  不是整个控制周期，也不代表已经保持 30 Hz。日志保留请求、回包、下发开始和返回时间。

实际负载、丢包和长尾验收未通过时不启用真机授权模式。默认模式仍与基线一致。

## 备份

本机外部记录根目录：`/home/ysyx/FPGA_competition/local_environment_record/`。

| 内容 | 路径 | 范围 |
| --- | --- | --- |
| Git 基线 | `git_baseline_20261114/flexifold_working_baseline.bundle` | 源码、RTL、位流、配置、依赖清单 |
| 运行资产副本 | `rollback_runtime_20261114/freeze_report.json` | 模型及两套 Python 环境，约 37.7 GB，独立副本、逐文件 SHA-256 |
| 实板副本 | `rollback_board_20261114/board_snapshot.json` | 64 个实际文件，包括 bit/HWH、配置、Python 和字体 |
| 本阶段验收 | `fpga_authority_phase1_20261114/` | 板端快照复核、本机测试和实板测量 |

系统驱动、系统 Python、OS 没有归档，也不在本次修改范围。
Cookie 不进入 Git 或板端快照；开发目录仅在内存中复用原工作区已有登录。
每次有差异的板端同步还会在 `runtime_snapshots/board/` 先完成完整快照，失败则不停止/上传。
各开发目录共享原演示的设备操作锁，防止任务、互动与验收同时抢占板卡。

## 不驱动机械臂的实板验收

结束任务或互动后，可从 AI PC 普通终端或已授权硬件访问的 Codex 会话运行。
当前访问限制已解除，后续联合负载、双端显示和恢复结果见 [HARDWARE_ACCEPTANCE.md](HARDWARE_ACCEPTANCE.md)。

```bash
bash /home/ysyx/FPGA_competition/flexifold_working_baseline/tools/run_fpga_benchmark.sh
```

该入口不导入 Piper/CAN，不向机械臂发命令。先记录原服务状态，再备份、同步板端 Python，
选择 `synthetic_test + isolated_lcd + bulk_vectors`，测量 750 次真实 FPG1 往返；
新会话中先放行一条内存目标，再用 J2=2.65 的诊断副本验证 PL 拒绝与后续锁定。
最后恢复之前的服务来源和模式，并检查 FUI1 响应；如果原服务未运行，自动启动诊断服务，
测完恢复为停止状态，无需先手动启动服务。
正常通过要求匹配结果、20 ms 窗口内完成和拒绝目标的接收回调为零。
报告给出 P50/P95/P99/最大值及逐帧数据，不用 PL 周期数代替端到端时间。

这份报告固定 `eligible_for_policy_execution=false`：它是边界测试，不能冒充真实相机、NPU、
ROCm、模型与完整控制入口同时工作时的验收。板端帧缓冲匹配也不等于实体屏幕已经目视验收。

### 2026-11-14 22:03 实板结果

上述普通终端入口已在真实 B35 运行通过，原始记录位于外部记录目录的
`fpga_authority_phase1_20261114/board_benchmark.json`，对应实际帧缓冲为 `board_benchmark.lcd.png`。

| 项目 | 实测 |
| --- | --- |
| 往返样本 / 匹配 | 750 / 750 |
| 20 ms 窗口超时 | 0 |
| 往返 P50 / P95 / P99 / 最大 | 2.670 / 3.435 / 3.865 / 8.106 ms |
| 内存接收端 | 正常目标接收 1 次；被拒绝目标接收 0 次 |
| 拒绝依据 | 实际 PL MASK=5，越界及步长异常，参考结果一致 |
| 拒绝后的下一条正常目标 | 已锁定，未请求新许可、未下发 |
| LCD | 独立 PID 6069，服务 PID 6051；当前会话/序号/MASK 与 PNG 哈希匹配 |
| 恢复 | 测前服务停止，测后已恢复停止；没有机械臂命令 |

先前两次未通过的结果也保留：首次为服务未启动的前置限制；第二次在 LCD 初始化等待期间
尚未进入 UDP 监听。已分别补上自动启动/原状态恢复，以及异步 LCD 初始化与启动阶段诊断。
不能将这轮空载诊断数据直接当成完整控制负载下的速度或时序保证。

复核截图后，AI PC 节点文字从“动作已送达”改为“数据已送检”，避免误认已经驱动执行器。
该文案已在本机按实际板端快照渲染检查，将随下次同步部署；本轮实板 PNG 保留原始内容。

## 真机授权开关

`required.example.json` 有意保留 `qualification_report=null`，不能用于真机执行。
启动入口在复位和机械臂连接之前运行轻量预检，缺少验收即停止。
`AIPC_FPGA_AUTHORITY_CONFIG` 指定配置后，每条策略目标都必须通过 FPGA；未设置则使用原入口。

执行验收记录必须具有 `fpga_authority_qualification_v1`、真实 PL 身份、当前主机启动身份、
主机实现/板端实现/guard 配置 SHA-256、实际地址、FPS、反馈时限与窗口预算，
并满足 `full_controller_load_measured=true`、零窗口超时、故障注入通过。
程序会核对当前板端来源为 `host_observation`，且优化模式和实现哈希与验收一致。
相同实现的正常服务重启不需要仅因 PID 变化重做验收。
目前尚未产生这份完整负载记录，不应手写 passed 来绕过检查。

后续顺序：先核实实板边界数据和 LCD → 在完整控制负载下做不执行候选的时序/故障验收 →
经现场受控试验启用策略授权 → 再用按完整轮次划分的成功/失败图像标定视觉复核。
硬件 CAN 网关和独立看门狗属于另一个需要接线及输出验证的阶段。

## 回退

先结束任务和互动。原演示目录依旧保留，可继续从该目录进入原面板。
Git 代码取回使用新目录，避免覆盖开发成果：

```bash
cd /home/ysyx/FPGA_competition/flexifold_working_baseline
git worktree add ../flexifold_baseline_restore baseline-before-fpga-authority-20261114
cd ../flexifold_baseline_restore
python3 tools/restore_dependencies.py --source-workspace /home/ysyx/FPGA_competition
```

板端代码属于外部状态，切换 Git 不会自动恢复。若已上板，需要在无任务时停止受管服务，
用快照清单核对并恢复静态文件，再从原入口启动；不能直接恢复包含旧 PID 的动态 service_state。
位流、模型与原 Python 环境本阶段不变。运行资产副本用于原资产损坏时恢复，
不要把现场环境改成指向备份目录的链接，也不要在备份环境内安装依赖。
