# FPGA侧运行与后续真机验收

代码准备于2026-11-01。新Python统一服务仅完成本机软件验证，尚未部署到板卡或另一台Linux AI PC。包内LCD1+B32位流保持此前已通过板测的文件身份。

## 文件与角色

- `board/runtime.py`：唯一guard写入方；共用原协议接收循环，运行独立LCD线程，结束后恢复B32。
- `screen/view.py`：LIVE/REPLAY共用状态模型与渲染。LIVE表示当前服务刷新，不等于真实机器人数据；来源标签另外显示。
- `host/task_status.py`：HOST/OPERATOR阶段声明，FUI1，不影响动作检查或喂狗。
- `host/coop_demo.py`：合成流程联调，包含思考、抓取、两次折叠和完成；不发机器人指令。
- `host/watchdog_demo.py`：既有诊断异常与PERIODIC/ON_RESULT测试。
- `host/runtime_report.py`：导出显示时延、阶段声明、接受COMMIT间隔CSV。

同一服务只接收一个FWD1会话和一个FUI1发布会话。新统一runtime收到FWD1 END后结束本次运行并恢复B32；原headless入口保留手动停止语义，用于协议调试。需要显示COMPLETE时，发布阶段后至少保留一帧显示时间再发送END；示例保留1.1秒。

## 板卡部署（留待真机阶段）

把`board_runtime.zip`上传到Jupyter根目录，在已有root终端执行：

```bash
cd /home/xilinx/jupyter_notebooks
mkdir fpga_offline_001
/usr/local/share/pynq-venv/bin/python3 -m zipfile -e board_runtime.zip fpga_offline_001
cd fpga_offline_001/board_runtime
sha256sum -c SHA256SUMS
bash scripts/run_fpga.sh AI_PC_IPV4 synthetic_test runs/bench_001
```

`AI_PC_IPV4`应替换为未来实际发送机器的有线IPv4，不能照搬旧Windows开发机地址。运行目录必须是新的；测试另一个会话时重新启动并换一个目录。Python来自板上PYNQ venv，目标环境已知为Python3.8/Pillow7/NumPy1.20。

服务默认UDP9009，不新增网络端口。启动前检查四个bit/hwh文件。各维护入口协作使用临时目录中的全局PL进程锁，在加载位流前排除第二个服务；手动Jupyter/vendor程序不遵守此锁，使用这些工具前仍需停止服务。

## Linux AI PC联调（留待真机阶段）

解压`aipc_client.zip`，进入`aipc_client`目录。发送与CSV导出仅需要Python3.8及以上标准库，不要求AI PC安装PYNQ、Vivado或Pillow。

```bash
python3 -B -m host.coop_demo --udp FPGA_IPV4:9009 --out runs/coop_001
```

将`FPGA_IPV4`替换为实际板卡地址，最近一次开发地址为192.168.2.99。此示例用ON_RESULT模式：超过500ms的THINKING不会触发周期动作超时。抓取/折叠文字均为合成发送事件，不是机器人视觉识别结论。程序最后发送END，板卡退出并回退B32。

需要完整PERIODIC异常探针测试时，使用原headless入口（END后仍保持端口，用于拒绝尾部非法探针）。先在板端另建运行目录并执行：

```bash
mkdir runs/periodic_001
/usr/local/share/pynq-venv/bin/python3 -B -u -m board.pynq_udp_server --bitfile overlay/lcd_framebuffer_20261101/lcd1_guard.bit --peer AI_PC_IPV4 --log runs/periodic_001/board.jsonl
```

然后在PC执行：

```bash
python3 -B -m host.watchdog_demo --udp FPGA_IPV4:9009 --mode periodic --out runs/watchdog_001
```

完成后在板端Ctrl+C停止headless服务。统一runtime的PERIODIC/暂停/恢复/END及UI并发另由本机端到端测试覆盖。不要同时运行两个服务，独占锁也会阻止此操作。

实际业务可复用`host.task_status.Client((ip,9009))`，在同一发布会话中调用`send(phase,activity,origin)`并在结束时`close()`；枚举见`docs/OFFLINE_COMPLETION.md`。该方法为同步网络I/O，应在独立遥测线程调用，禁止直接放进机械臂控制tick。状态回包只确认PS接收声明，发送超时由上层记录。

## 停止与回退

正常会话END、前台Ctrl+C或Linux SIGTERM走统一清理流程：停用watchdog，停止并等待显示线程，关闭LCD输出，再恢复B32。显示失败或端口占用会以非零码退出，并写失败记录。不要用SIGKILL作为正常停止方式；进程/内核无法执行清理时，不能保证自动回退。

默认前台运行方便首次联调。需要后台常驻时可用nohup启动同一命令，保留控制台输出，并以SIGTERM正常停止；开机自动启动的实际路径、网络就绪与账号配置留到目标板部署时核对。本轮没有修改SD启动镜像或系统服务。

## 日志与报告

- `board.jsonl`：收包、检查结果、拒包、超时和阶段声明。
- `frames.jsonl`：实际提交的帧代数、Python绘图/写屏时长、所用watchdog快照与阶段。
- `latest.png` / `latest.json`：最近提交画面与数据；PNG不是屏幕实拍。
- `final_state.json`、`exit.json`、异常时的`failure.json`：最终状态、回退读回与故障。

将整份运行目录从板卡拷回后，在AI PC或开发PC执行：

```bash
python3 -B -m host.runtime_report --run BOARD_RUN_DIR --out reports/run_001
python3 -B -m host.watchdog_report --pc PC_events.jsonl --board BOARD_board.jsonl --out reports/paired_001
```

第一个命令导出frames.csv、task_claims.csv、commit_gaps.csv和summary.json；第二个关联FWD1双端会话。显示更新间隔、MMIO COMMIT调用间隔、UDP往返和控制tick是不同量。COMMIT间隔统计在PAUSE/RESUME/END处断开，不能跨暂停拼接。生产控制周期及最终watchdog门限仍待真实采样；500ms保持旁路诊断初值。

## 真机验收清单

1. 在目标板跑完整runtime，确认LIVE与synthetic标签、颜色/方向、阶段文字和退出回退。
2. 测量持续刷屏与UDP并行时的往返分布、实际画面更新间隔、CPU/SD负载和长时间稳定性。
3. 在Linux AI PC运行合成流程和PERIODIC/ON_RESULT测试，保存双端原始日志。
4. 确认模型/控制分别在哪台主机运行，再选择真实采样位置与阶段事件来源。
5. 最后跟随机器人旁路采样；超时仅诊断，不影响动作。没有逐通道结果时不显示14个绿色通过灯，不能从零违规推断动作安全。
