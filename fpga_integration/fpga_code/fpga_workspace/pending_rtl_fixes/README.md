# Historical B34 RTL fixes

> 这三个修订已在当前 B35 实现、重新生成位流并通过实板回归。本目录保留 B34 问题复现与原始补丁。下面文字描述修订前状态，当前发布见 [B35 report](../../../../docs/FPGA_B35_RELEASE_zh.md)。

这些源代码是待 FPGA 工程师重新综合、实现和生成位流的补丁，**没有加载到当前 B34，也没有替换活动 rtl/ 下与交接位流匹配的源码**。

1. fold_stage：断流计数原先饱和在阈值，retreat_valid 使用 <= 比较，因而永不失效。修复为计数可到阈值+1。
2. fold_vision：相邻帧间隔应与 last_frame_time 比较，不应与 stable_first_time 比较。面积/中心仍对首帧锚定，稳定跨度仍对首帧计时。
3. axi_lite_guard：补足视觉快照拼接中的4个零位，使 stable_count 真正落在 [23:16]。当前已交付B34位流实际落在[19:12]，活动PS桥接已按实板布局修正；新位流必须配套改回16位偏移。

还需补齐 phase config CRC、标定配置与现场形态门限，再完成发布验收。该目录不代表已实现这些额外要求。补丁之后不得直接拿当前 .bit 配合新寄存器解释使用。

`b34_required_fixes.patch` 可在 fpga_workspace 根目录以 `git apply --check pending_rtl_fixes/b34_required_fixes.patch` 检查适用性，已通过检查；不要对当前运行版本直接应用。

新增 `tb_retreat_expiry_regression.sv` 和 `tb_frame_gap_regression.sv` 可分别与原始/待修模块编译运行。Icarus 12 实测：原始模块在新增用例如预期失败，待修模块通过。前者用强制计数加速到阈值，后者输入相邻250ms三帧。完整编译与运行日志位于工作区 `local_environment_record/fpg1_v3_update_20261113/rtl_simulation/`。这些是 RTL 单元回归，不是重新综合、实现或时序验收。
