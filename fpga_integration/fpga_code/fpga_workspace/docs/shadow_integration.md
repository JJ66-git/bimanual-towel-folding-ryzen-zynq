# 只记录的 FPGA 旁路接入设计

## 目的与边界

此阶段只把主机已经完成原有检查的策略样本复制到 FPGA，记录两边的检查结果与延迟。主机保留原来的校验、使能、复位、CAN 和动作发送行为。FPGA 回包、超时、队列满或服务异常都不能改变 `target`，不能跳过原检查，不能阻止、替代或重发 `send_action`。

这不是安全联锁，也不是机器人控制权限的改变。回放链路通过不代表真实运动路径已经被保护。

## 采样位置

源码快照 `source_snapshot/piper_towel_replica/deployment_entry/cobot_hybrid.py` 的主循环中，`validate_absolute(target)` 与 `validate_execution(...)` 均成功后、`wrapper.send_action(action_dict(target))` 之前，是策略样本的候选采样点（约第 622-638 行）。

采样内容必须是同一拍的本地副本：最终 `target`、`measured`、上一最终目标 `previous`、`act_action`、当前及上一 `correction`、step、flags、profile 和本机时间。向量应立即转成不可变 tuple/整数，不能把 NumPy 数组引用交给后台线程，因为随后 `previous = target` 且 correction 会继续更新。

采样记录另带 `candidate_only` 或 `dispatched` 元数据：`args.execute` 为 false 时，记录是候选动作而非真实下发动作。FPGA v1 帧本身没有这个字段，必须由主机日志记录，不能从 `RUNNING` 或 PL phase 推断是否已发令。

## 线程与队列

控制线程只执行快照构造和 `put_nowait`，不创建 socket、不等待网络、不轮询 PL、不等待工作线程。`put_nowait` 不等待队列空位，但 Python 队列和状态计数使用短时共享锁，不能承诺硬实时的绝对零等待。队列使用有界容量；原型建议先用 32 项，并将实际最大深度作为配置和日志字段。

队列满时拒绝新样本并递增 `queue_full_drops`，不得等待队列空位、覆盖旧样本或复用上一次 FPGA 结果。这样保留已排队的先后关系，丢弃情况显式可见。原型应维护 `enqueued/sent/replied/timeouts/errors/response_mismatches/queue_full_drops/shutdown_drops` 计数，并记录最高队列深度。

`submit()` 在入队前把协议样本复制成不可变整数快照；样本类型、维度或数值无效时只递增 `errors` 并返回 `False`，不得把旁路异常抛回控制调用方。

单个后台 worker 独占一个长期 UDP socket 和一条 PYNQ session，按序发送请求并等待匹配回包。每拍请求的 session/sequence/tick 必须单调关联；v1 服务锁定首个 host/session，因此不得为每个 tick 启动一个 `host.link` 进程。UDP 超时后记一笔缺失结果，不自动重试旧 sequence；不允许把旧回包配给后续 tick。序号回绕或 worker 重新启动时，应明确结束旧会话并重启板端服务。

关闭流程不应让策略循环等待网络。`close()` 默认不等待；在非控制线程调用时可传入有限清空期限。超过期限后丢弃并计入 `shutdown_drops` 的排队样本，当前在途交换最多等自身 UDP 超时后结束，worker 不再发送后续队列项并自行关闭 socket。

## 请求字段来源

| 字段 | 来源与规则 |
| --- | --- |
| `target`, `measured`, `previous`, `act`, `correction`, `previous_correction` | 使用采样点的本拍数组副本；先走原主机校验，再量化。不要传模型归一化张量或未过滤候选 |
| `profile_crc` | 与 Overlay 固化参数对应的配置 CRC；启动时先比对，profile 不同则旁路标记配置错误，不发送伪装成有效样本的帧 |
| `step`, `RUNNING` | 来自主机循环状态；不把循环次数或 `scheduled_phase` 当成任务成功 |
| `CHECK_CORRECTION` | 按当前实际校验语义确定是否有有效 correction，不只看是否进入过 Smol 阶段 |
| `MANUAL_HANDOFF` | 只有主机能确认该拍存在有效手动接管请求时才置位；自动 step 交接与手动请求需区分 |
| `CHECK_START` | 只有原语义要求检查起点且并非 resume-current 时置位；需要和原始起点检查条件一致 |
| `queue_depth` | 只有能读取该拍 RTC 实际队列深度时才发送；否则用 `UNKNOWN`，不能用 `step >= 300` 推断非空 |
| `feedback_age_us` | 只有反馈样本和本机 monotonic 时钟有可靠映射时才发送；否则用 `UNKNOWN`，不以读取缓存的时间冒充采样时间 |
| `elapsed_us/tick_us` | 由主机 monotonic 时钟生成，只用于关联和测量，不与 PL 自由运行计数器直接相减 |

如果 profile、feedback age、queue depth、manual handoff 或 start/resume 状态当前没有可靠来源，应保持未知或不启用对应检查，并在日志说明。不能猜测字段填充后宣称检测有效。

## 结果与错误处理

- 收到回包后先校验 CRC、type、profile、session、sequence 和 tick，再和本拍样本配对。
- 对每条已配对记录比较 `violations/events/phase`，记录 FPGA `board_processing_us` 和主机 `send -> matching reply` 时间；两种时间单独命名。
- 不匹配（包括错序、错 profile 或损坏回包）、timeout、socket 错误、PL 超时、队列满和关闭期限丢弃都只增加旁路诊断计数。原控制线程既不接收结果，也不等待 worker。
- 旁路错误不得改变 `target`、`previous`、correction/authority、handoff 状态、reset/enable 状态或动作发送次数。
- `violations=0` 仅说明这些比较器没报错，不是动作授权、系统安全或任务完成标志。

## 分阶段实施与验收

1. 运行当前 39 项主机测试；其中 10 项覆盖旁路队列异常、localhost UDP worker 闭环、真实 UDP 错序回包和完整 trace 回放 CLI。
2. 单独运行 `python -B -m host.shadow_replay --trace examples/real_trace_excerpt.json --out replay_shadow_20261031`，确认 667 条样本逐帧匹配、没有队列丢弃或超时，并检查输出摘要和记录文件。
3. 此回放的对端是本机整数参考模型，不是 FPGA。确认离线结果后，再实现独立的板端 replay CLI，使用单个 worker/session 将历史样本送到 PYNQ；不把工具接入实时运控程序，也不连接机器人 CAN/使能。
4. 若未来做 live shadow，对照运行原有 host 检查与 FPGA 结果，观察数据覆盖、违规差异、丢帧/timeout、队列水位、CPU 占用和每 tick 延迟。任何一个旁路指标都不能成为动作 gate。

实时运行验收前至少需要连续运行测试、明确异常和退出计数，并确认原控制路径的动作数量、目标值与不启用 FPGA 时完全一致。任何最终发令控制方案都属于另一个阶段，必须重新做整机风险和状态机设计。
