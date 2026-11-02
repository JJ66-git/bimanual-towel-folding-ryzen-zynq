# 与当前运控的接入位置和开发顺序

只记录旁路的采样/队列/异常处理设计见 [SHADOW_INTEGRATION.md](SHADOW_INTEGRATION.md)；在其验收完成前不改变动作发送语义。

本包新增实现均独立于原工程，未修改真机脚本。第一步建议旁路比较，主机继续执行原检查；记录 FPGA 检查结果及延迟，先处理数值和时序差异。

## 1. 接入点

`cobot_hybrid.py::main` 已计算 ACT、correction、raw_target、最终 target，并在 `validate_execution(...)` 后、`wrapper.send_action(...)` 前拥有当前一拍需要的所有位置向量。这里是策略动作检查请求的候选接入位置：

```python
# 接入示意，不是已经修改的生产代码。
# 原 validate_absolute(target) 和 validate_execution(...) 继续保留。
# 拷贝同一拍数据，不共享随后会被修改的数组。
sample = Sample(
    profile_crc=profile_crc(profile),
    step=step,
    flags=RUNNING | (CHECK_CORRECTION if blended else 0),
    elapsed_us=elapsed_us,
    feedback_age_us=UNKNOWN,  # 有可靠同源时间戳之前保持未知
    queue_depth=known_queue_depth_or_UNKNOWN,
    target=quantize(target), measured=quantize(measured),
    previous=quantize(previous), act=quantize(act_action),
    correction=quantize(correction),
    previous_correction=quantize(previous_correction),
)
# 将 sample 送到独立有界日志/通信队列，先只对照结果，不增加运动路径。
```

此处仅是接入位置，示例没有定义线程、背压和生产 deadline；本包的 host.link 是回放工具，不能直接塞进 30 Hz 控制循环同步等待。旁路队列满或丢帧也应明确计数，不能用上一帧结果代替本帧。后续若改成串行阻断式检查，必须单独测量端到端时延、处理缺失回包及确认最终发令语义。

主循环之前 `connect_holding_measured` 会发保持/使能，reset 使用另一套接口，退出保持也有独立发送分支。因此仅拦截上述一处并不能覆盖所有硬件命令。若目标是覆盖全部命令，需要统一的发送层并定义 reset、连接保持、策略和退出保持各自合法状态，不能在包内一个新比较器上宣称全系统保护已经完成。

## 2. 信息来源

- target/previous 必须是最终过滤和包络检查对应的同一数据层，不能把模型归一化张量或未约束候选当成机械臂目标。
- measured 来自该拍观测，角度为 rad、夹爪为 m；effort 不是本请求的位置向量，不参与角度比较。
- previous_correction 使用该拍更新前的实际 correction 副本；接管后首次请求或跳采样时不能假设上一修正为零。
- queue_depth 应读取 RTC 引擎实际队列。step≥300 不证明队列已有数据；空队列是原有 fallback 情况，不能不经设计直接变成新的整机停止条件。
- joint/gripper feedback age 必须先确定 SDK 时间来源和时钟转换。当前一条 age 标量只有在能表示该拍所有相关反馈最大年龄时才有明确语义；否则传 UNKNOWN，后续可升级协议逐侧/逐字段年龄。
- 启动时间、主机 tick 与 FPGA 自有计数器分开记录。时间戳只供关联/测时，不能隐含跨设备时钟同步。

## 3. 分阶段验证

1. Vivado 2018.3 已通过 1,678 组 motion_guard 向量和 axi_lite_guard 总线集成仿真，并为 正点原子启明星 V2 的 `xc7z020clg400-2` 生成新版 PYNQ Overlay。FCLK 设为 50 MHz，AXI 地址端口为 16 位，布局布线后的 WNS 为 `+5.864 ns`、WHS 为 `+0.100 ns`，无 setup/hold 失败端点。新版位流已通过 PYNQ 冒烟、1,678 组独立 PL 向量和 667 帧实体板 UDP 全量参考比较。
2. PYNQ 板端的 MMIO/协议测试通过。`/dev/uio0` map 不包含 guard，故使用 PYNQ Overlay MMIO。主机 ping 4/4 成功；单帧、10 帧及四轮 667 帧 UDP 回放均收到响应，最新 16 位地址位流的 667/667 `violations/events/phase` 自动参考比较通过。一个已知越界样本返回 `violations=1`，坏 CRC 报文被板端拒绝且未回包。最新板端处理时间 min/median/mean/p95/max 为 2086/2134/2211.081/2366/10743 us，主机 UDP 往返为 3779.5/4699.5/4921.616/6882.4/16461.1 us。该回放未接入实时控制循环。用户确认过改动前的 29 项主机测试；当前源码静态计数为 39 项，待在项目 Python 环境复测。
3. 比较定点结果与主机浮点结果，重点扫每个阈值两侧及符号/整型极值。现有 1,417 步记录无差异只覆盖这两个样本。
4. 增加旁路采样与结果日志，保留所有原检查，测每轮请求延迟、漏帧/重复/过期和 CPU 占用。不能把任务折叠失败归因给旁路 FPGA 或反过来宣布保护有效，除非有对应证据。
5. 明确是否需要硬件介入最终发令，再设计模式状态机、失联处理和独立硬件接口。当前包不包含这一步的生产部署。

## 4. 板卡确定后需要补充的工程信息

开发板已确认为 正点原子启明星 V2，丝印 `XC7Z020-CLG400ABX2545`，并已能启动 PYNQ/Jupyter。当前 Overlay 只用 PS AXI 与 PL，不依赖 LCD/外部 PL 引脚。新版 16 位 AXI 地址位流已通过实体板 PYNQ 冒烟、1,678 组 PL 回归、已知违规样本、坏 CRC 丢弃和 667 帧 UDP 全量比较。当前开发顺序以 [PROGRESS.md](PROGRESS.md) 为准；网络接口地址以该板实际系统设置为准。
