# 指标口径与证据

`metrics.csv` 的已测值取自项目原机日志与 FPGA 构建报告；它只整理汇总值，不构成原始证据。用户要求不打包日志、原始图像和数据，故竞赛正式提交前应独立审核并补充相应原始证据。`待测` 不是零，也不是指标达标。

最近一次运行的 `trace.json` 记载 750 步、`execute=True`、`termination_reason=action_budget`；这只说明控制程序到达预算，不说明折叠任务成功。时延分位数若标“最近秩”，计算为排序后第 `ceil(p*N)` 个；P50 使用中位数。相邻 tick 不是传感到机械物理响应的端到端时延，FPGA 板端处理时间包含 Python/MMIO，不是纯 RTL 延迟。

`tools/summarize_metrics.py` 读取用户单独提供的原始 `trace.json`、`summary.json` 和 `checks.jsonl`，只输出聚合结果。不要把含原始视频、状态或密钥的路径一并上传到公开仓库。
