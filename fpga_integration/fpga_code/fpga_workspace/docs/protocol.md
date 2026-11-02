# FPGA 检查请求/响应协议 v1

## 1. 帧与字节序

所有多字节字段为 little-endian。UART 采用 8N1；UDP 每个 datagram 恰好放一帧。

| 偏移 | 字节数 | 字段 |
| --- | --- | --- |
| 0 | 4 | ASCII `FPG1`，字节 46 50 47 31 |
| 4 | 1 | version=1 |
| 5 | 1 | type：1 请求；2 响应 |
| 6 | 2 | payload_length：请求 364，响应 20 |
| 8 | 4 | session，主机每次通信会话产生随机 uint32 |
| 12 | 4 | sequence，会话内递增；回包原样回显 |
| 16 | 8 | tick_us，主机请求时间戳；回包原样回显 |
| 24 | N | payload |
| 24+N | 4 | CRC32，覆盖头部和 payload，不含 CRC 自身 |

CRC 以 `zlib.crc32(bytes)&0xffffffff` 为准，测试串 `123456789` 的结果是 `0xcbf43926`。实现常用反射多项式 0xedb88320、初始寄存器 0xffffffff、结果异或 0xffffffff，并将最终整数按 little-endian 发送。完整请求 392 字节，响应 48 字节。

CRC 负责传输完整性，不是身份认证。session 只防止误配旧会话回包，不是加密令牌。板端需要额外维护新请求顺序；本包没有 UART/MAC/CRC 解析 RTL。主机的 response_matches 校验 type/session/sequence/tick/profile 五项。

## 2. 请求 payload

| payload 偏移 | 类型 | 字段 |
| --- | --- | --- |
| 0 | uint32 | profile_crc，见配置标识 |
| 4 | uint32 | step，当前零起始策略步 |
| 8 | uint32 | flags |
| 12 | uint64 | elapsed_us，当前任务相对起点经过时间 |
| 20 | uint32 | feedback_age_us，已知同源反馈年龄；未知用 0xffffffff |
| 24 | uint32 | queue_depth，已知 RTC 可用候选数；未知用 0xffffffff |
| 28 | int32[14] | target，本拍最终目标 |
| 84 | int32[14] | measured，观测位置 |
| 140 | int32[14] | previous，上一条最终目标 |
| 196 | int32[14] | act，ACT 基础动作，已完成原 processor 后处理 |
| 252 | int32[14] | correction，当前已约束的 Smol 修正 |
| 308 | int32[14] | previous_correction，上一拍已约束修正 |

flags：bit0 CHECK_START；bit1 CHECK_CORRECTION；bit2 RUNNING；bit3 MANUAL_HANDOFF。其余位 v1 拒绝。CHECK_CORRECTION 对应原程序“当前 correction 绝对值最大值>1e-4”的执行分支，不能仅依据 source 字符串。CHECK_START 仅用于实际需要起点校验的阶段，resume-current 等语义由主机决定。reset 模式尚未定义独立请求。

向量索引：0…5 左关节、6 左爪、7…12 右关节、13 右爪。角度乘 1,000,000 后为微 rad，夹爪 m 乘 1,000,000 后为微 m；不是角度制。主机按绝对值四舍五入、负值恢复符号，NaN/Inf/超 int32 范围直接拒绝编码。

每个量化误差最大半个单位，两个量相减的误差可达一个单位。绝对限位额外 1 微单位，关节步进/跟踪仍按编码后的原阈值比较。定点临界判定可能与原 float32 不同，边界附近不能宣称逐位等价；部署前应建立边界对照和明确的保守处理策略。当前实现用于旁路对照，不取代原保护。

elapsed_us 和 tick_us 都由主机提供，FPGA 不能直接用自己的自由运行计数器与它们相减。独立看门狗使用 FPGA 自己的时钟，从最后一次完整合法新请求开始计时。

## 3. 响应 payload

依次为五个 uint32：`profile_crc, violations, events, scheduled_phase, board_processing_us`。软件 emulator 最后一项固定 0，表示未模拟板端处理耗时，不是测得零延迟。core 自身不接收 session/sequence/tick，这些元数据必须由外层封装与同一笔请求配对后回显。

violations 位定义：

| bit | 名称 | 含义 |
| --- | --- | --- |
| 0 | target_bounds | 最终目标越绝对包络 |
| 1 | act_bounds | ACT 基础目标越包络 |
| 2 | joint_step | 十二关节 target-previous 超限 |
| 3 | tracking | 十二关节 measured-previous 超限 |
| 4 | correction_magnitude | CHECK_CORRECTION 时修正幅值超限，属于管线不变量复查 |
| 5 | correction_slew | CHECK_CORRECTION 时修正变化超限 |
| 6 | gripper_ownership | 最终夹爪不等于 ACT 或夹爪修正非零，属于新增显式复查 |
| 7 | start_pose | CHECK_START 时起点误差超限 |
| 8 | action_budget | RUNNING 时 step≥max_actions，属于调度违规提示 |
| 9 | feedback_stale | 新增可选反馈年龄超限，当前配置关闭 |
| 10 | profile_mismatch | 请求与硬件配置不一致 |

events：bit0 交接条件已到期/手动请求；bit1 动作预算已到期；bit2 名义 blend 阶段且已知队列为 0；bit3 反馈年龄未知。UNKNOWN 队列不当作零，未知反馈年龄不伪造通过时效检查。events 没有任务成功位。

scheduled_phase：0 非运行；3 ACT 名义阶段；4 已到交接条件。它不区分实际 RTC 是否有候选、取消接管或布料处于哪一折，不能代替主机真实状态机。

## 4. 配置标识与数值宽度

`config/guard_profile.json` 按 `json.dumps(profile,sort_keys=True,separators=(',',':'),ensure_ascii=True)` 编码，再算 CRC32，得到 profile_crc。当前值见 `rtl/guard_config.svh`，每次改配置必须重新运行 `python -m host.make_vectors` 并同步 FPGA 常量。CRC 标识用于检错，不提供安全签名。

Python 减法无固定宽度溢出。RTL 对两个 int32 做减法时先符号扩展到 33 位，绝对值也使用 33 位，覆盖 -2^31 到 2^31-1 的极端差值。常见错误是先在 32 位中溢出，再取 abs，导致大跳变被误判为很小。

## 5. 板端封装必须实现的行为

1. 完整接收固定长度，校验 magic/version/type/length/CRC，再将所有 payload 字段作为同一快照锁存。
2. 校验 profile、会话和序号。旧帧/重复序号不能刷新看门狗；新会话的开始与重新武装应由上层状态机明确处理，不在运行中自动接受任意会话重置。
3. 使用 core 的 in_ready 握手，等待 out_valid 后获取结果；在此之前不能把旧结果配给新序号。
4. 返回同一笔请求的 session/sequence/tick。主机遇到坏帧、旧回包或超时只报告通信错误；本示例没有执行运动的权限路径。
5. 将 valid_new_request 接到独立 watchdog。示例 TIMEOUT_CYCLES 只是占位参数，实际值由板卡时钟、链路延迟和反馈时效实测决定；当前没有给生产运动指定断链动作。
