# Watchdog diagnostic stream — implementation brief

2026-11-01。仅 FPGA/PYNQ 与独立 PC 发送器；不接机器人。

## 最小设计检查（编码前）

1. 结构：将周期动作观测和 FPG1 检查分成不同魔数，避免任意检查或心跳被当成动作刷新。
2. 调用负担：新增 FWD1 的 op/session/sequence/tick；动作复用现有 Sample，不新增第二套关节编码。BEGIN 只选择 PERIODIC 或 ON_RESULT。
3. 可推导项：板侧自行读取年龄、超时次数、状态及间隔；PC 不填 FPGA 周期或假定 30 Hz。状态查询不刷新。
4. 最小范围：复用现有单进程 UDP/PYNQ 服务，增加状态机与 JSONL；不用新线程、数据库、依赖或新位流。
5. 通用性：覆盖周期观测、暂停恢复和非周期结果流；不按某次折叠或特定 tick 编写规则。

最小实现：既有 FPG1 原样传输；同一 UDP 端口新增 FWD1，服务单独锁定其地址/session 并强制 seq/tick 单调。所有 MMIO 由一个进程顺序访问。仅靠收到 FPG1 不足以判断动作流生命周期，所以必须有独立的显式事件。

## 合同

- BEGIN(seq=0, mode=PERIODIC/ON_RESULT) 开始诊断会话。BEGIN 只启用模式，不写 COMMIT；不能用新的 BEGIN 偷换已锁定会话。会话结束后重启服务才能开始另一会话。
- ALLOW/HOLD 都携带完整 Sample；先校验长度、CRC、源、session、seq、tick、字段和 profile，再经 PL 检查并记录。在 PERIODIC 中写一次 ACTION_COMMIT。PL 违规位仍仅为诊断，不充当动作授权或 COMMIT 的安全门控。
- 此消息表示发送方声明的“已接受动作观测”。板端能验证帧与字段，不能证明机器人真的收到了动作；真实发送采样点尚未接入。当前发送器全部为 synthetic diagnostic demo。
- 相同目标的新 HOLD 允许刷新，重复 seq 或旧 tick 不允许刷新。传输序号允许跳跃，缺号记日志；不重试旧动作。
- PAUSE 仅从 PERIODIC 进入暂停，清除 PERIODIC。RESUME 仅从暂停恢复 PERIODIC，等待新动作才武装。
- ON_RESULT 不启用周期门限。STATUS 是可用的心跳/查询，但不刷新动作流。不能因 VLA THINKING 自动 PAUSE；异步推理时动作流可能仍应持续。
- END 和进程退出清除 PERIODIC。raw expired 位/累计次数遵循现有 RTL 的锁存语义，不用软件伪造复位。
- 到期只写 JSONL 诊断，禁止机器人导入、使能、发令、急停或网络控制行为。
- FWD1 CRC 与 peer 限制不是身份认证；保持当前受限实验网络。模式帧与日志不发送图像、模型或凭据。

## 编码前列出的失败模式

1. 错魔数/版本/操作、短长帧、坏 CRC、非法模式、无效 Sample、错 profile。
2. 非允许 peer、非 BEGIN 首包、首序号非零、重复/倒序 seq/tick、换 session/来源端口、重复 BEGIN、结束后再发。
3. 无首动作时误到期；STATUS/FPG1/坏包刷新；HOLD 不刷新；到期反复加计数；新动作不能恢复。
4. 未暂停直接 RESUME、暂停时动作、ON_RESULT 中 PAUSE/RESUME；失败操作先写寄存器或消费序号。
5. 暂停/ON_RESULT 超时误报，恢复后未等待首动作；退出留 PERIODIC 使能。
6. 接收空闲时无到期日志，持续 UDP 时不轮询；32 位年龄读回跨边界不一致。
7. 日志覆盖旧文件、丢失来源/seq/step/时间，或直接相减不同设备 monotonic 时钟。

## 验证

先从真实 CLI 进入，通过 localhost UDP 和外部 PYNQ 硬件边界替身验证生命周期、错误帧与日志；替身通过测试目录 PYTHONPATH 提供，生产程序没有 fake 模式。纯协议边界单独测试。然后将同一生产源码部署到实际 B32，运行合成生命周期与 ON_RESULT；保留 PC/板端 JSONL。最后重新运行 667 帧 FPG1 参考比对，确认原协议不受影响。500 ms 仍为暂定诊断值。
