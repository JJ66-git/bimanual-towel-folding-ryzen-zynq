# AMD AI PC 与 FPGA 现场展示指南

当前入口覆盖 AMD 硬件及离线 NPU 视觉模型演示、设备盘点、FPGA 合成协议演示和控制命令预览。**真实机械臂任务与新加入的 FPGA 实时旁路尚未在这台 AI PC 完成联调**；下面把可现场演示的内容与待完成的部分分开写。

## 开场和准备

系统构成：一台 AMD AI PC 运行 ACT、SmolVLA、融合控制和设备 I/O；两只 Piper 使用 USB-CAN、三路相机通过 USB、Zynq-7020 FPGA 通过网线接入。旧 Cobot 主机不在最终链路中。

需要演示 AMD NPU 时，在运行菜单的同一个终端执行：

```bash
cd /run/media/zachary/program/FPGAcompition
sudo prlimit --pid $$ --memlock=1073741824:1073741824
bash npu_deploy/run_showcase.sh
```

第一行的路径对应原机；复制到其他电脑时改成项目根目录。memlock 是每个进程可锁定的内存上限，不是预先申请 1 GiB。模型视觉演示默认 `systemd` scope 内存上限 16 GiB；可在启动菜单前设置 `export AMD_DEMO_MEMORY_MAX=20G`。这不是总物理内存保证，设备共享内存未必全部计入。

## 1. 证明 AMD 平台与推理（菜单 1）

按照提示逐步按 Enter，输入 q 可退出本段。脚本实时运行 `lscpu`、`lspci`、AMD-SMI、ROCm 矩阵计算和 CPU 结果对比、`xrt-smi examine`/`validate`，最后检查模型文件哈希，并用 NPU 对三路固定图像做视觉编码。现场展示命令、结果和新建的日志目录。

建议讲解：“CPU、核显和 NPU 都在这台 AMD AI PC。这里的 ROCm 矩阵运算与 NPU 视觉推理是现场执行的。NPU 负责 SmolVLA 视觉编码器，guided RTC 等部分仍由 ROCm 路径处理。”`cuda:0` 是 PyTorch 对 ROCm 设备使用的命名，设备名与 HIP 版本用于判断实际硬件。NPU 自检 TOPS 不是整个 VLA 的 TOPS；此处三张固定图片也不是机械臂闭环任务。

## 2. 展示设备连接（菜单 2、3）

选 2 盘点 USB、CAN、视频设备与依赖；选 3 查询相机身份及格式。以现场结果为准，检查左右 USB-CAN 和三路相机的角色绑定，不按 `can0` 或 `video0` 编号猜测。菜单 5、6 只打印复位和 hybrid 命令，`--check-only` 不加载模型、不访问设备。

## 3. 演示 FPGA 网络及合成计算（菜单 4、8）

**提前在板端**确认 PYNQ 已启动、LCD/网线接好，确定 FPGA 的实际 IPv4，AI PC 和板卡之间的实际路由。历史交接中板端运行包已经解压；实际位置和服务状态需现场查看。板端进程未启动时，进入该运行包目录，在板端终端执行：

```bash
# 板卡终端；先进入实际的 board_runtime 目录
sha256sum -c SHA256SUMS
bash scripts/run_fpga.sh AI_PC实际IPv4 synthetic_test runs/本次新目录
```

示例中的 `AI_PC实际IPv4` 必须换成 AI PC 到 FPGA 路由所用的源 IPv4；运行目录必须是新的。板端程序使用服务实际 UDP 端口 9009 并占用前台；不要另开第二个 overlay 操作程序。可在 AI PC 执行 `ip route get FPGA实际IPv4` 查询 `src`。

菜单选 4 输入 FPGA 地址，可做 ping；它只说明 IP 可达。选 8 同样输入地址，会显示并调用 `host.coop_demo`：发送一组**合成的**任务阶段和动作，检查 FWD1/FUI1 回包、build ID 与动作检查结果，输出每次往返时间和日志。LCD 的文字是主机声明的合成阶段，并非视觉识别或机器人完成折叠。

演示成功后客户端发送 END，统一板端服务会结束会话并尝试恢复基线 overlay；再演示需在板端用新目录重新启动。若中途失败，查看本次 AI PC 日志和板端日志，确认会话状态再处理。响应和 build ID 可以表明收到匹配协议的结果，但还应结合板端服务日志和 overlay 信息证明实际 PS/PL 执行；不能单靠 ping 或静态界面认定 FPGA 在运算。

也可直接启动合成演示：

```bash
bash npu_deploy/demo_fpga_synthetic.sh FPGA实际IPv4
```

## 4. 展示真机任务：后续联调完成后

现有菜单**不会执行真实机械臂动作**。完成两臂和三相机的现场绑定、控制及重置链路检查后，可从原项目的 `run.sh reset`、`run.sh hybrid --backend rocm` 或实验 `run_hybrid_npu.sh` 继续；`--execute` 会使对应入口进入实际动作流程。运行方式以现场设备配置和操作者判断为准。

AI PC 控制循环现已增加可选 FPG1 旁路，已做真实观测的实体板只读对照，运动期间尚待验收；运行 `AIPC_FPGA_SHADOW=FPGA_IPV4:9009 bash npu_deploy/run_hybrid_npu_fpga.sh` 可选择 NPU 视觉、ROCm 控制和 FPGA 旁路组合（默认只读）。FPG1 不喂 FWD1 watchdog。完成真实采样和对照前，不能把 FPGA 合成屏幕称作真实折叠过程，也不能把它写成正在为机器人控制回路提供许可。待联调后，可按“硬件/模型证明 → 设备状态 → FPGA 实时旁路 → 机器人完成任务”的顺序演示，并展示相应现场日志。

## 源码与记录

- `demo_amd_ai_pc.sh`、`demo_rocm_compute.py`：AMD 平台和 GPU/NPU 演示。
- `demo_fpga_synthetic.sh`：调用交接的 `host.coop_demo` 并读取本次协议回包；实际客户端在 `fpga_integration/aipc_client`，公开源码包中在 `fpga/`。
- `run_showcase.sh`：菜单入口。
- 日志：`npu_deploy/artifacts/demo/` 下每次新建独立目录；板端日志在其运行目录。
- 技术细节参见公开源码包中的 `docs/TECHNICAL_ZH.md`。

## 5. 2026-11-02 本机实时旁路验证

一次 ACT 只读验证，使用现场三路相机与两臂状态，AI PC 向实体板发送 10 条 FPG1 候选动作，10 条均返回且与整数参考一致。该次 FPGA 板端处理约 2.1–2.5 ms、主机往返约 5.9–9.1 ms，样本只有 10 条，不能外推为尾延迟或整机性能。该次未发送机械臂动作，没有使用 NPU 视觉编码，不证明任务完成。

板端 FPG1 固定第一个发送端和会话，新进程开始下一轮前须重启板端统一服务。当前 FPG1 不触发 FWD1 watchdog。初次 NPU 实时集成运行在模型编译阶段被中断并出现 CPU 回退，原因尚待独立排查；可运行 `npu_deploy/run_npu_preflight.sh` 查看完整编译日志及三路固定图像结果。

## 6. 两个现场入口（2026-11-02 更新）

重连设备之后，先在 AMD AI PC 的项目根目录运行：

```bash
bash npu_deploy/prepare_showcase.sh
```

该脚本按阶段等待 Enter，把现场动作预算设为 750，与 FPGA 工程 profile 对齐；检查左右 CAN 和三路相机绑定、被动监听反馈、检查 FPGA IP，并进行独立 NPU 视觉预检。准备中可能请求 sudo，不使能或移动机械臂。每次准备会写独立日志。`task_start.json` 的原始记录帧数保留，不用于覆盖此次 750 步预算。

正式运行前，PYNQ 上需有新启动的 `host_observation` 服务。若旧终端仍在运行，先在该终端按 Ctrl+C 使它退出并回退，再在板端运行准备脚本打印的命令，例如：

```bash
cd /home/xilinx/jupyter_notebooks/fpga_offline_001/board_runtime
bash scripts/run_fpga.sh AI_PC实际IPv4 host_observation "runs/live_$(date +%Y%m%d_%H%M%S)"
```

板端 IP、AI PC IP 和运行目录以现场为准。统一服务的 FUI1/FPG1 会话锁定使每轮正式运行前都需新服务；脚本不会假装它能自动启动远程板卡。默认 FPGA 地址在本机入口中配置，可用环境变量 `AIPC_FPGA_SHADOW=IPv4:端口` 覆盖。

在 AI PC 上开始正式工作：

```bash
bash npu_deploy/start_showcase.sh
```

该命令会**实际使能和移动机械臂**。它在四个阶段分别等待 Enter：依次展示 AMD-SMI、ROCm 矩阵计算和 XRT；检查 FPGA FUI1 确认；在你确认后倒计时五秒并慢速复位；复位成功后再次等待你的 Enter，才启动最多 750 步的 NPU 视觉 + ROCm hybrid。每个等待点可输入 `q` 退出。只有出现 `RESET_EXECUTION_COMPLETE` 才会进入任务确认。策略连接机械臂前，在同一个 FPG1 会话发送非运动探针，要求 PL 回包和整数参考一致；运行期间 FPGA 仍为旁路，不对动作或 watchdog 门控。NPU 的 `NPU_PARTITION_CONFIRMED` 是子图分配，`NPU_FIRST_INFERENCE_COMPLETED` 才是第一次 NPU 视觉推理完成。原策略在第 300 步开始 SmolVLA handoff，不代表一定接纳 SmolVLA 候选。

两个脚本均支持 `--check-only`，仅打印流程，不访问设备或更改配置。运行期间 Ctrl+C 请求停止：程序检查停止标志，尝试发送当前测得的保持姿态，不自动失使能；如果正在等待驱动、GPU 或其他阻塞操作，响应可能延迟，也不能保证物理保持成功。现场急停和机械支撑由操作者掌握。

现有测试覆盖源码级中断和模拟脚本联动；两入口尚未在重新连接的完整机械臂和 FPGA 上做端到端验收，不能预先声称任务成功。正式运行日志包含现场图片/状态，保存在项目私有日志目录，不应自动加入源码分享包。
