# 可复用工作流

这里描述已存在的工具脚本，不虚称已打包为经过认证的独立 Agent Skill。

1. `npu_deploy/prepare_showcase.sh`：绑定设备、检查反馈、唤醒 FPGA 屏幕/诊断服务、运行 NPU 预检。依赖硬件、控制密钥和本机环境；CAN 配置可能要求 sudo。
2. `npu_deploy/start_showcase.sh`：经用户分段确认后展示 AMD、检查 FPGA、复位并执行最多 750 步。会使机械臂运动；仅适合经过现场验证的设备与工作区。
3. `npu_deploy/run_dashboard_readonly.sh`：20 秒真实相机和 SDK 查询，无运动目标；不得与其他占用相机的进程同时运行。
4. `npu_deploy/fpga_repeat_check.py`：验证 FUI1 与 FPGA PL 诊断通道的连续会话，不访问机械臂。
5. `npu_deploy/run_policy_check_bounded.sh`：离线固定输入 NPU/ROCm 对照；依赖权重、导出文件及原环境。

上述工作流目前基于当前机器的路径、设备模型和网络配置，尚无第二设备、第二场景或全新环境部署成功的独立证据。失败时不应自动放宽运动限制；检查日志、设备身份和用户现场状态。
