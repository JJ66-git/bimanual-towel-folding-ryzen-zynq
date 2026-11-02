# Watchdog 周期流诊断与验证

更新：2026-11-01。新的 FWD1 诊断服务已部署，并通过实体板验证；本机 46 项通过。实际时钟问题已修正并校准。完整结果见 [本轮实测报告](../../validation_runs/20261101_watchdog_stream/PHYSICAL_VALIDATION.md)。

## 实现与边界

`board/pynq_udp_server.py` 现在使用同一 UDP socket 接收 FPG1 检查与 FWD1 诊断事件，由同一进程独占寄存器写入。原 FPG1 请求 392 B、响应 48 B，不改字段或行为。新增 `--log` 与 `--peer` 为必填参数：必须提供新的 JSONL 文件和允许来源。旧版启动命令需要补日志参数。

FWD1 的 ALLOW/HOLD 表示发送方声明的动作观测。板端验证帧、profile 和新鲜度，再运行 PL 检查；PERIODIC 模式下写一次 ACTION_COMMIT。它不证明机器人收到了动作；PL 违规仍只记录，不能据此授权或停止动作。本轮 PC 工具全部标记 `synthetic_test`，没有机器人导入或实际下发。

500 ms 沿用 B32 的 25,000,000 周期初值，FCLK 50 MHz；本次没有修改 RTL 或位流；HWH 做了与真实启动 PLL 对齐的时钟元数据适配。当前接入只用于诊断实验，不给 VLA 思考设定完成期限。

## 协议

固定小端 header 与 FPG1 结构相同，魔数为 `FWD1`。header 为 `<4sBBHIIQ`，24 B；末尾 CRC32 为 4 B，覆盖 header 与 payload。version=1，CRC 不提供身份认证。

| Header 字段 | 类型 | 定义 |
|---|---|---|
| magic | 4s | FWD1 |
| version | u8 | 1 |
| op | u8 | 请求 1–7；响应为请求 op OR 0x80 |
| payload_length | u16 | 下表指定的精确长度 |
| session | u32 | PC 每次新会话生成；服务重启前不允许替换 |
| sequence | u32 | 首包 0，后续严格增加；允许缺号，记录 sequence_gap |
| tick_us | u64 | 发送方本地 monotonic 微秒，严格增加；不和板端计时直接相减 |

| op | 名称 | payload | 整帧 |
|---|---|---|---:|
| 1 | BEGIN | `<I` 模式，1=PERIODIC、2=ON_RESULT | 32 B |
| 2 | ALLOW | 与 FPG1 相同的完整 Sample | 392 B |
| 3 | HOLD | 与 FPG1 相同的完整 Sample | 392 B |
| 4 | PAUSE | 空 | 28 B |
| 5 | RESUME | 空 | 28 B |
| 6 | STATUS | 空 | 28 B |
| 7 | END | 空 | 28 B |

全部响应为 72 B，payload `<IIIIQQIII`（44 B），依次为 build_id、watchdog status、timeout_count、软件状态、age_cycles、last_gap_cycles、violations、events、phase。控制/查询未运行 motion_guard，后三项为 `0xffffffff`，不能解读为违规。

软件状态：0=未开始，1=PERIODIC，2=暂停，3=ON_RESULT，4=已结束。watchdog 原始状态位仍为 bit0=PERIODIC、bit1=armed、bit2=expired。

## 生命周期

- BEGIN(PERIODIC) 开启预期，但等待首个新 ALLOW/HOLD 才武装。BEGIN(ON_RESULT) 不启用固定周期门限。
- ALLOW 和 HOLD 都需要完整有效帧、正确 profile、已锁定来源/session、递增 seq/tick。目标不变的新 HOLD 可以刷新；重复帧不刷新。
- 检查结果只记录。ALLOW/HOLD 是发送方标签，不是 FPGA 许可；不能从“零违规”推断机器人动作安全。
- STATUS 可作为通信查询，不写 COMMIT。FPG1、坏帧、旧序号、其他 session、错 profile 都不刷新 watchdog。
- PAUSE 只在 PERIODIC 接受；关闭周期预期。RESUME 只在暂停接受，恢复后重新等待首个动作。
- ON_RESULT 不接受 PAUSE/RESUME，因为没有周期预期。改变流模式需结束会话并重启服务；当前没有在线模式切换兼容层。
- END 关闭周期预期，锁住已结束会话。正常退出、Ctrl+C 或 Linux SIGTERM 也尝试关闭；SIGKILL、断电或进程被强制终止不能保证清理。
- 原 RTL 停用时保留 expired/累计计数历史；不能把软件暂停当作硬件统计复位。last_gap 是锁存值，恢复后的首个动作可能仍显示上一个间隔，不能当成本拍新测量。
- VLA THINKING 不是自动暂停依据。异步推理可能仍持续发动作；周期流断档只记原因未知，不直接判断网络断开或模型失败。

服务空闲和持续收包期间均轮询原始 watchdog；名义轮询间隔 50 ms。PS 调度、MMIO/PL 检查或系统负载可能使日志观测更晚，PL 计数器才是超时是否发生的依据。响应快照中多个寄存器读取不是硬件原子快照，64 位计数使用 high/low/high 防低位翻转。

## 代码与日志

| 文件 | 职责 |
|---|---|
| host/watchdog_protocol.py | FWD1 严格长度/版本/操作/CRC 编解码 |
| board/watchdog_monitor.py | 状态机、原始计数读取、COMMIT、JSONL |
| board/pynq_udp_server.py | 单进程服务、来源限制、两类协议独立会话 |
| host/watchdog_demo.py | 明确标注 synthetic 的独立发送与异常探针 |
| host/watchdog_report.py | 双端按 session/sequence/tick 关联，校验回包并导出 CSV |

PC `events.jsonl` 使用 perf_counter_ns，记录 session/seq/tick、host_sent_ns、host_received_ns、RTT 和返回寄存器。板端 JSONL 记录收到时间、PL 检查调用前后、PS COMMIT 写调用前后、样本 step/target、检查位图、模式/暂停/结束、超时与拒包。

PS 的 COMMIT 调用时间不是精确 PL 接受边沿；实际间隔以 PL gap cycles 为准。不同设备 monotonic 时钟不能直接相减；没有校时就不能宣称单向网络时延。板卡墙钟仍未校准，日志只使用板端 monotonic。未知/坏帧的 header_hex 明确为不可信原字节，不能当已接受事件。

## 本机验证

在 `C:\robot\qiansai\FPGA_part\fpga_workspace` 执行：

```powershell
python -B -m unittest discover -s tests -v
```

当前 46/46 通过。新增端到端测试从生产服务 CLI 和发送 CLI 进入，使用真实 localhost UDP；仅 PYNQ 硬件边界由 `tests/fixtures/hardware_boundary/pynq.py` 替代。生产程序没有 fake 开关。PERIODIC 验证 13 次交换、4 次动作刷新、一次超时及恢复、10 次预期拒包；ON_RESULT 验证 6 次交换、2 次动作观测、0 COMMIT、0 超时及 5 次预期拒包。还验证报告 CLI 输出与固定预期摘要。

纯协议测试覆盖损坏、长度、版本/操作、非法模式/flags；计数读取覆盖低位翻转与不可稳定读取。

## 已验证部署与复现

实体板已通过：PERIODIC、ON_RESULT、667 帧 FPG1、watchdog smoke 与 1,678 组 PL 向量。原始记录及 CSV 见 [PHYSICAL_VALIDATION](../../validation_runs/20261101_watchdog_stream/PHYSICAL_VALIDATION.md)。

使用当前工作区的 pynq_udp_runtime.zip，包内已经包含正确位流、修正后的 HWH 和黄金向量；不要再按旧微包步骤从旧 flexiguard_b32 复制旧 HWH。部署按 [PYNQ_RUNTIME](PYNQ_RUNTIME.md)。

当前板端目录为 /home/xilinx/jupyter_notebooks/flexiguard_watchdog_stream_20261101，本轮所有服务已停止。下一次启动时需创建新日志文件。PC 发送命令保持不变，--out 必须使用新目录：

```powershell
python -B -m host.watchdog_demo --udp 192.168.2.99:9009 --out PATH_TO_NEW_PERIODIC_DIR --mode periodic
python -B -m host.watchdog_demo --udp 192.168.2.99:9009 --out PATH_TO_NEW_ON_RESULT_DIR --mode on-result
```

两条命令之间重启板端服务；下一次 host.link 回放也重启。下载 JSONL 后运行：

```powershell
python -B -m host.watchdog_report --pc PATH_TO_PC_events.jsonl --board PATH_TO_BOARD_board.jsonl --out PATH_TO_NEW_REPORT_DIR
```

本次已生成 report_periodic/events.csv 与 report_on_result/events.csv；它们是实体板与合成输入的结果。真实生产动作流尚未接入，实际生产 COMMIT 间隔尚未测量。

时钟依据、首次失败和修正见 [时钟诊断](WATCHDOG_CLOCK_DIAGNOSIS.md)。500 ms 是经当前硬件时钟校准的暂定诊断值，仍不是生产门限。
