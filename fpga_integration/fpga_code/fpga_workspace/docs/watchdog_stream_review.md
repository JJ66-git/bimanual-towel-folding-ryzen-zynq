> 最终实体板结果已通过；时钟元数据适配、PC 高精度计时和构建回归见 [时钟诊断](WATCHDOG_CLOCK_DIAGNOSIS.md) 与 [实测报告](../../validation_runs/20261101_watchdog_stream/PHYSICAL_VALIDATION.md)。

# Watchdog stream delivery review

范围：逐行复核本清单中的新增生产代码、修改后的 UDP main、测试和硬件边界替身；没有声称完整仓库审计。板端部署文件与 ZIP 8/8 一致，Python 3.8 AST 通过。原生产 RTL/位流未改动。

## 最小设计门检查

- 新 op、session/seq/tick、BEGIN mode 是独立动作生命周期所必需；其余 Sample 重用，周期与计数由 PL 读取，不让调用方填写假定周期。
- Monitor 有单个服务调用方，但必须集中持有已锁定 session、序号和模式，避免分散在 UDP 分支中的状态。它没有可替换 backend 或可选策略。
- Client 是诊断 CLI 的有状态 socket/session 所有者；多次交换与负面探针共享同一个真实 socket，不能由每次发送重新创建会话。foreign-session 参数仅用于该工具真实发出的负面探针，不进入板端生产配置。
- Journal 同时被服务和 Monitor 使用，统一 JSONL 及文件独占创建；report CLI 和用户 CSV 是其实际消费者。
- 编解码的 response 参数区分两种真实报文方向；不是 backend/fallback。FPG1 与 FWD1 的 profile 检查各在对应入口执行，避免协议间相互依赖；不增加泛化校验框架。
- 软件 state 区分暂停与 ON_RESULT，二者的原始硬件 enable 均为零，不能只用原始状态推导。没有额外暂停理由、模型类型或任务阶段字段。
- 不增加线程、队列、数据库、阈值配置或第三方依赖；没有测试专用生产模式。测试替身仅由 tests/fixtures 的外部 PYNQ import 边界注入。
- 原 FPG1 字节格式保留；统一服务新增 --log 必填，全部当前部署说明同步，没有维护第二套可选旧服务分支。
- JSONL/CSV 区分 PC 时钟、PS 写调用与 PL gap，不跨设备直接相减；raw_last_gap 明确是锁存值。

## 检查结果

1. 原 39 项与新增 6 项均通过，共 46；端到端覆盖 PERIODIC/ON_RESULT 与报告 CLI，预期稳定摘要匹配。
2. 无首动作、STATUS/FPG1、坏 CRC、错 profile、重复 seq、旧 tick、换 session 均不构成有效 COMMIT；暂停/恢复和 HOLD 语义已在本地验证。
3. 64 位读回覆盖 low 翻转重读；不稳定读回会报错，不返回拼接错误计数。
4. 生产文件不导入机器人、CAN、使能或控制逻辑；结果仍仅诊断。
5. 来源限制与 JSONL 新文件要求是显式启动参数；拒包保留不可信 header 原字节，不把它当已接受事件。
6. 打包与 SHA 校验通过；新包只包含生产代码和配置，不包含测试 PYNQ 替身。

## 残余限制

- 新服务已完成 PERIODIC/ON_RESULT、667 帧回放和 50 MHz 1,678 向量实体板验证；所有输入仍为 synthetic_test，不能作为真实机器人接入证据。
- 启动前必须人工确认没有其他 MMIO 服务或 Notebook 写入者；此实现未加入跨进程 PL 全局锁。旧进程仍运行时不能启动并重载 Overlay。
- 正常退出尝试清除 PERIODIC；强制终止/断电不保证清理。日志不是硬实时，磁盘或 PS 阻塞可延迟观测。
- ALLOW/HOLD 是发送方观测声明，不验证真实机器人执行。live 采样点、生产门限、长期压力及小屏幕仍未完成。

代码增量相对收到的交付，按区域计数如下（仅本轮代码清单，不含文档和上轮测试导入修复）：

| 区域 | 新增 | 删除 | 净增 |
|---|---:|---:|---:|
| board | 181 | 9 | 172 |
| host | 343 | 0 | 343 |
| tests | 235 | 0 | 235 |

增长用于真实协议/生命周期、诊断 CLI、日志关联和对应失败场景；没有新增 RTL 或通用框架。源码复核范围：

- board/pynq_udp_server.py
- board/watchdog_monitor.py
- host/watchdog_protocol.py
- host/watchdog_demo.py
- host/watchdog_report.py
- tests/test_watchdog_protocol.py
- tests/test_watchdog_stream.py
- tests/fixtures/hardware_boundary/pynq.py
- tests/fixtures/watchdog_expected.json

本轮追加复核：overlay/own_hybrid_motion_guard_watchdog.hwh 的四项时钟参数、build_pynq_overlay_watchdog.tcl、scripts/adapt_pynq_hwh_clock.tcl，以及两项时钟回归。运行时 HWH 适配是必要的已验证 boot preset；不修改实际 CPU/PLL。原位流未改动，未声称重新综合。
