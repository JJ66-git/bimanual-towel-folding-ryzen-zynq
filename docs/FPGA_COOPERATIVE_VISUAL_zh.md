# FPGA cooperative display

2026-11-13：B35 增加由真实任务消息和 PL 结果驱动的协作界面。板端 LCD 与电脑网页使用同一份板端快照；页面优先说明当前结果，再提供可核对的证据。

## Display and interaction

LCD 原生 800×480，竖屏逻辑画布 480×800。采用暖纸色、炭黑、朱橙和黄绿色；当前阶段明确写出“当前：张爪／夹持／翻折”，已完成节点打勾，后续节点保持灰色。阶段状态无效或停更时显示“上次”，避免把旧结果当作当前任务。框内主要字号为 18–34 像素，主结论 40–44 像素；圆圈数字按实际字形居中。

协作模块随任务改变：电脑“动作已送达”，FPGA“已经检查”，屏幕“板屏已显示”；说明串联同一条任务编号。没有动作数据时显示等待，停止更新后保留上次结果并明确提示。探针只表示连接验证，不计为任务执行或动作检查。FPGA 成功发出回复不等于电脑控制进程已经收到回复，界面不作后者的声明。

折叠检查使用“等待双臂退开”“正在检查折叠”“已观察到折叠”“暂时无法确认”等说明。稳定帧数、事件编号和实际被 PL 接受的画面编号作为证据；电脑宣告完成不能直接生成 FPGA 完成结果。阶段识别依赖有效的两臂状态，不代表已经抓住毛巾。

网页提供任务总览、数据与证据、板端屏幕三个页签，支持冻结／恢复实时显示、导出 JSON、复制诊断与屏幕全屏。电脑分工区展示实际 CPU 负载与已验证的 ROCm、NPU 环境身份；未采集的实时 NPU 利用率不填造数值。

## Evidence semantics

| 信息 | 实际来源与含义 |
| --- | --- |
| 动作已送达／已经检查 | 板端已接受的 FPG1 请求及对应 PL 回复；以会话和任务序号关联 |
| 当前阶段 | PL 阶段值、有效位、有效反馈时间和最近任务样本共同决定 |
| 未发现数据异常／具体异常 | 该笔 PL 检查掩码；不是机器人运动许可 |
| 发出时距更新 | 电脑发出这条动作时报告的状态更新时间差，停更后保留该笔值；不是现在的年龄或网络延迟 |
| FPGA 计算用时 | 回复中的真实硬件周期数与 50MHz 时钟 |
| 整板响应用时 | PS 从收到请求到发出回复的处理时间，不含电脑与板卡网络往返 |
| 板屏已显示 | 帧缓冲成功提交后发布的快照；PNG SHA256 与 JSON 绑定 |
| 连接中断 | 网页停止取得新快照；保留历史并显著标记，不生成新结论 |

板端显示线程每秒最多刷新两次，只读取状态快照；协议线程持有 PL。网页服务只读取 Jupyter 内容接口，不发送任务消息，不写硬件寄存器。

## Start and release

在项目根目录执行：

```bash
bash npu_deploy/fpga_board/open_dashboard.sh
```

电脑入口为 <http://127.0.0.1:8765>。`control_jupyter.py start` 默认启动 `host_observation` 并显示 LCD；更改来源需要先停止当前版本服务。运行脚本继续使用原有准备／运行入口。

显示代码位于 `fpga_integration/fpga_code/fpga_workspace/screen/`，板端桥接位于其 `board/fold_display.py`，网页与只读桥接位于 `npu_deploy/fpga_board/`。发布快照位于 `fpga_integration/release_candidates/fpg1_v3_b35_20261113/`。

新包为 `npu_deploy/artifacts/fpga_board/releases/b35_cooperative_ui_20261113.zip`，包含 100 项文件及 SHA256 清单。原 `b35_pc_update.zip` 保留。此次 UI 更新不修改 B35 bit/HWH、硬件时钟或阶段配置；回退材料保留。字体为随包提供的 Noto CJK 子集及许可，已兼容板端 Pillow 7。

## Validation and limits

项目回归 83 项通过，其中协作状态测试 15 项。真实 UDP＋实际 PL 合成序列覆盖等待、张爪、夹持、翻折、视觉等待／检查／观察结果及异常。浏览器验证覆盖桌面、390px 手机、冻结／恢复、14 项输入查看、JSON 导出、板屏图像、键盘页签、焦点、减少动效与断连提示。

证据位于工作区 `local_environment_record/fpga_visual_ui_20261113/`。`final_live_sequence/dynamic_visual_validation.json` 记录当次序列和截图名；对应 PNG 与 JSON 哈希绑定。`cooperative_lcd_synthetic.gif` 是这些实际板端截图的回放，输入明确为 SYNTHETIC，不代表现场机械臂折叠。未发送机器人动作命令。

此前实体屏方向、颜色、边框与闪烁已由用户确认；此次最终字号的实体屏目视可读性尚未重新确认。相机、机械臂、现场视觉标定和完整任务验收仍待接入验证。

收尾已恢复 `host_observation`，任务样本为零，屏幕显示等待任务开始；关闭开发用 8766 预览，正式网页 8765 保持运行。最后只读检查的软件、FPGA 通信、配置身份、板端日期和既有 LCD 确认均通过，板端相对主机时差约 −0.12 秒；待项为相机、两臂 CAN 与现场身份核验。

本轮按 [Impeccable](/home/ysyx/.codex/skills/impeccable/SKILL.md) 与 [make-interfaces-feel-better](/home/ysyx/.codex/skills/make-interfaces-feel-better/SKILL.md) 的可用规范设计与复核。Impeccable launcher 因执行权限未运行，已直接读取上下文；未声称完成概念种子或用户批准构图流程。设计约定记录在网页的 `DESIGN.md` 与 `.impeccable/design.json`。
