# Linux AI PC 迁移与后续开发

2026-11-01。AI PC已确认为另一台Linux电脑，系统版本、Python版本和有线IP尚未确认。本次提供离线迁移包；没有连接该AI PC、安装软件或修改其网络。

## 工作放在哪里

| 设备 | 当前职责 | 需要的软件 |
|---|---|---|
| Linux AI PC | Python源码主开发、发包、模型/控制接口接入、日志分析与报告数据导出 | Python；完整软件测试/离线预览还需Pillow和NumPy |
| 启明星7020 | 运行现有PYNQ、PL检查器/watchdog、LCD显示 | 现有PYNQ镜像和配套板端Python；沿用已验证环境 |
| 当前Windows开发电脑 | 保存原始交付，运行已验证Vivado2018.3硬件构建 | Vivado；只在需要生成新.bit/.hwh时使用 |

当前阶段Linux AI PC不必安装Vivado，也不需要在x86 AI PC上安装PYNQ来操作远程板卡。它通过UDP与板上服务交换数据，通过SSH/SCP或Jupyter管理板上程序。Jupyter是访问入口，不是Python开发的必需编辑器。

Python界面、日志处理和发送端改动通常不需要重新生成位流。RTL、AXI寄存器结构、引脚或扫描硬件变化则需要重新构建并重新验收；目前构建脚本验证环境是Windows/Vivado2018.3，未宣称已验证Linux/Vivado构建。

## 解压并检查

把迁移包复制到AI PC后，在Linux终端执行（压缩包实际位置若不同，请替换第一条tar命令的路径）：

```bash
mkdir -p ~/projects
tar -xzf ~/Downloads/fpga_linux_handover_20261101.tar.gz -C ~/projects
cd ~/projects/fpga_linux_handover_20261101/fpga_workspace
python3 --version
ip -br addr
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r screen/requirements.txt
python -B VERIFY_PACKAGE.py
```

发送端代码按Python3.8语法组织；完整50项测试此前在Windows/Python3.12运行通过。`VERIFY_PACKAGE.py`已移除Python3.9才提供的`Path.is_relative_to`调用，使用兼容写法。Linux真实系统的依赖安装和50项完整测试仍需在目标机执行后才能确认，不把Windows测试称作Linux实测。

venv用于这份工程的测试与预览，避免直接改动已有模型环境。没有网络时可先校验包内SHA256SUMS；UDP发送端本身不依赖Pillow/NumPy。不要在AI PC执行`board/pynq_*`来代替板上服务；其PYNQ/MMIO访问属于板端。

## 不接机器人也能先检查的内容

```bash
mkdir -p ../work_runs
python -B -m screen.replay --log tests/fixtures/screen_events.jsonl --manifest tests/fixtures/screen_manifest.json --out ../work_runs/offline_preview.png
```

这生成480×800离线界面和JSON摘要，不访问FPGA，画面明确标REPLAY。输出文件不能重名覆盖。现有测试发送端是`host.watchdog_demo`，无需另写协议或引入ROS/模型依赖。

## 接板前确认网络

板卡上次验证IP为192.168.2.99/24，当前Windows开发PC为192.168.2.10/24。Linux AI PC要能路由到板卡；先查看真实网卡/IP，不直接照抄开发PC的地址。与Jupyter相通不自动证明UDP端口及peer配置正确。

板端服务的`--peer`必须改为AI PC实际有线IPv4，不能继续保留旧开发PC的192.168.2.10。迁移文件不会自动修改板卡IP、路由器或服务参数。AI PC的具体网卡配置命令需要根据`ip -br addr`结果确定。

## 分别运行板端与AI PC端

板上已有验证目录为`/home/xilinx/jupyter_notebooks/lcd1_validation_20261101`。若重新部署，迁移包的`board_runtime/`含独立运行ZIP，保留旧目录，解压到独立目录。下面板端命令中的AI_PC_IP示例必须替换成目标机实际地址。

板端现有root终端：

```bash
cd /home/xilinx/jupyter_notebooks/lcd1_validation_20261101
mkdir -p validation_runs/aipc_periodic_001
AI_PC_IP=192.168.2.20
/usr/local/share/pynq-venv/bin/python3 -B -u -m board.pynq_udp_server --bitfile overlay/lcd_framebuffer_20261101/lcd1_guard.bit --bind 0.0.0.0 --port 9009 --peer "$AI_PC_IP" --log validation_runs/aipc_periodic_001/board.jsonl
```

Linux AI PC，在`fpga_workspace`及已激活venv中：

```bash
python -B -m host.watchdog_demo --udp 192.168.2.99:9009 --out ../work_runs/aipc_periodic_001 --mode periodic
```

此命令只发送synthetic测试数据，验证首动作、超时、恢复、暂停等诊断流程，不调用机械臂。ON_RESULT测试前正常停止并重启板端服务，换新板端日志，再在AI PC执行：

```bash
python -B -m host.watchdog_demo --udp 192.168.2.99:9009 --out ../work_runs/aipc_on_result_001 --mode on-result
```

正常停止UDP服务会停用watchdog，但该命令本身不会恢复原B32位流。静态屏幕测试`screen.smoke`则有自己的回退流程。不要并行运行另一个会下载Overlay的程序，以免替换正在服务的PL。

这些命令尚未实现小屏实时显示进程：板上启动联合位流能使用LCD硬件，但现有屏幕软件仍是静态/回放入口，不能把UDP联调通过称作实时界面已完成。

## 收集并导出日志

每轮保留AI PC的`events.jsonl/summary.json`和板上的`board.jsonl`。有SSH权限后，可以从AI PC使用SCP把本轮board.jsonl下载到对应work_runs目录（板卡SSH账号/权限以实际环境为准，不把root Jupyter终端等同于root SSH已开放）。然后：

```bash
python -B -m host.watchdog_report --pc ../work_runs/aipc_periodic_001/events.jsonl --board ../work_runs/aipc_periodic_001/board.jsonl --out ../work_runs/aipc_periodic_report_001
```

报告需要的证据包括：双方原始JSONL、关联后的CSV、发送/回包序号、真实动作观测间隔、PS处理与UDP回放时延、watchdog启停/超时和数据来源。生产控制周期尚未测量；30Hz默认值和回放RTT都不能直接当作请求周期。500ms仍是旁路诊断测试配置。

## 后续开发的先后顺序

1. 实时小屏进程：读取当次日志及PL诊断，持续刷新；独立于收包/检查路径，持续刷屏不能阻塞发包/回包。
2. 在目标Linux AI PC完成PERIODIC和ON_RESULT在线联调，保留真实网络与环境记录。
3. 确认真实控制循环运行在AI PC还是Cobot主机；动作观测接入该循环，模型推理状态来自实际推理端。
4. 任务阶段由HOST/OPERATOR明确发送，不能由step/tick猜测抓取或折叠完成。
5. 常驻启动、退出/回退、长期运行和并发负载测试。

## 迁移后的Codex/开发会话

文件迁移不等于当前聊天和工具连接自动迁移。若在Linux AI PC打开新Codex会话，请打开迁移包根目录，先阅读`README_FIRST.md`、`HANDOFF_NEXT_STEPS.md`、`fpga_workspace/docs/PROGRESS.md`和最新实物验收报告，再继续开发。也可以保留当前Windows会话，经已授权SSH操作AI PC；这需要另行确认可达地址和现有登录方式。

`source_snapshot/`是此前控制程序的只读接口证据，不是目标Linux机器上已安装/已验证的实时控制环境。迁移包不携带模型权重、训练数据或整个旧工程归档，已有AI模型部署继续使用AI PC上的实际工程。
