# FPGA 当前开发进度

更新：2026-11-01。按用户决定，先完成代码，后统一真机联调；本轮没有连接或修改板卡/AI PC。

| 范围 | 当前状态 |
|---|---|
| FPGA侧软件功能 | 本阶段实现完成：统一UDP/异步LCD、显式任务阶段、日志与回退 |
| PL/显示硬件 | 沿用此前LCD1+B32位流，四个bit/hwh哈希不变 |
| 新本机验证 | 56/56通过；PYNQ设备边界为软件夹具，UDP/生产入口/画面写入API为真实程序 |
| 阶段显示 | HOST/OPERATOR声明经FUI1接收；不按tick猜测折叠成功，不喂狗 |
| 模式与并发 | PERIODIC/ON_RESULT、THINKING、暂停/恢复、显示延迟和异常退出已本机覆盖 |
| 资源独占 | 所有维护Overlay下载入口共用锁，防止第二服务在运行中重配PL |
| 新统一服务板测 | 待后续统一进行，不能以软件夹具测试代替 |
| 历史板测基线 | 静态/回放上屏获用户确认；原联合位流1678向量、watchdog、667帧UDP通过 |
| Linux AI PC | 已知为另一台Linux；IP/环境/真实模型采样位置待联调确认 |
| 生产间隔与门限 | 尚未测量/定标；500ms保留为旁路诊断初值 |

交付包括board_runtime.zip、aipc_client.zip、fpga_code.zip。进入真机阶段后按运行手册部署，先合成流程，再真实数据旁路和长期并发测试。新runtime按一次FWD1会话运行，END后恢复B32；headless入口保留调试拒包用途。

最后一次板卡收尾记录仍为B32、watchdog未武装；本轮没有重新查询或改变该状态。真实机器人动作仍不受FPGA诊断结果控制。

[离线完成报告](../../validation_runs/20261101_offline_complete/COMPLETION_REPORT.md) · [运行与验收手册](RUNBOOK.md) · [实现范围与协议](OFFLINE_COMPLETION.md)

[先前实体板验收](../../validation_runs/20261101_lcd_python/LCD1_BOARD_VALIDATION.md) · [旧阶段实现过程](../../validation_runs/20261101_lcd_python/LCD1_IMPLEMENTATION.md)
