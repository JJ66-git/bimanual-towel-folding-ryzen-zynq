# Clock and timing diagnosis — 2026-11-01

## 缺陷类别与规则

硬件周期数依赖实际运行时钟，不能只按 Vivado/HWH 声明换算墙钟时间。所有毫秒级往返测量必须使用具有足够分辨率的单调计时源；会话关联 tick 不等于真实控制周期。

## 证据与假设检验

原始可复现命令是 host.watchdog_demo --mode periodic。首次实体板在 quiet_timeout 时仍 status=3、age=21,168,513、count=0，测试失败；其后 PS 日志才记录到期。

| 候选原因 | 检验 | 结论 |
|---|---|---|
| HWH 与启动 PLL 不匹配 | 读 SLCR：IO_PLL_CTRL=0x1e000，FBDIV=30；HWH 为 FBDIV=54、div0=6、div1=6 | 确认运行时配置不匹配 |
| 周期换算不成立 | 约 250.56 ms 内计数 6,958,501，约 27.77 MHz | 25M 周期原来约 900 ms，不能称为 500 ms |
| 无效报文意外刷新 | 拒包和 FPG1 日志没有 action_observed/COMMIT；age 连续增长，last_gap 未变化 | 本次未见该原因 |

本地回归先变红：HWH 与实测 boot PLL 推导 27,777,777.5 Hz，未达到预期 50 MHz；CLI 回包的 RTT 为 0 也触发回归。修正后 46 项通过，原始未缩减的实体板场景通过。

## 修正

- HWH 仅改四项：FCLK0 divisors 6/6→5/4；IOPLL FBDIV 54→30；IOPLL 描述 1800→1000 MHz。实际 PLL/CPU 不被重新配置。
- scripts/adapt_pynq_hwh_clock.tcl 以当前目标板启动配置作为 runtime preset；构建脚本在导出 HWH 后调用，避免下一次构建重新带入默认 PLL 分频。该步骤是运行时元数据适配，不是新位流。
- 首次尝试直接写 Vivado 派生的 IO PLL 频率属性被验证脚本拒绝，未继续使用；失败日志保留，最终采用上述显式 runtime 适配步骤，Vivado Tcl 执行通过。
- PC 诊断发收时间与 RTT 改用 perf_counter_ns，本地硬件边界计数使用同一高精度类型。Linux 板端 monotonic 继续用于本机计时。

PYNQ 官方 [v2.7 时钟源码](https://pynq.readthedocs.io/en/v2.7.0/_modules/pynq/ps.html)提供分频设置，并在 Zynq 时钟实现中默认按 50 MHz 参考计算。板上安装源码也已读回：pynq_zynq_clock_source.txt。本板参考约 33.333333 MHz，因此 SDK 报告数不作为校准证据；修正后其 getter 显示 75 MHz，实际计数校准约 49.99 MHz。

## 扫描范围

搜索：rg -n 'time\.(monotonic_ns|perf_counter_ns|monotonic|perf_counter)' host tests/fixtures

| 位置 | 处理 |
|---|---|
| host/watchdog_demo.py | 发收、探针、RTT 和相关时基统一改为 perf_counter |
| tests/fixtures/hardware_boundary/pynq.py | 模拟周期计数改为 perf_counter_ns |
| host/link.py | RTT 已用 perf_counter_ns；其 monotonic tick 只用于严格递增的协议关联，不视为控制周期；现有超时截止轮询不是短 RTT 测量 |
| host/shadow.py | RTT 已用 perf_counter_ns；协议 tick 和等待截止同上，未改控制流程 |
| host/shadow_replay.py | monotonic 用于有界等待，不产出微秒级 RTT，非本次缺陷 |
| board/* | 在实际 Linux 单调时钟上记录，硬件年龄来自 PL；不跨设备相减 |

## 验证与限制

修正后约 250.45 ms 计数 12,519,551，约 49,988,943 Hz。PERIODIC 一次到期、恢复、暂停/重新武装通过；ON_RESULT 无周期到期；FPG1 667/667；50 MHz watchdog smoke 与 1,678 PL 向量通过。详见本次 PHYSICAL_VALIDATION.md。

这是依据板端时间的计数速率校准，并非示波器或外部频率基准测量。原 watchdog smoke 只覆盖宽松的功能等待范围，单独通过不能证明 500 ms；本次额外的时钟校准与生命周期测试提供对应证据。换镜像或 PS 初始化后必须重新校准。
