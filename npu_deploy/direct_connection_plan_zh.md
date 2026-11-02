# AMD AI PC 直连机械臂、相机与 FPGA 的计划

## 当前目标

由 AMD AI PC 本机同时运行 ACT、SmolVLA、原 hybrid 控制逻辑和设备 I/O；双 Piper 的 USB-CAN、三路相机及 Zynq-7020 直接或通过扩展设备连接到 AI PC，不依赖 Cobot 主机或双机网络。已取得 FPGA 动作规则旁路检查、watchdog、PYNQ 通信和 LCD 的工程代码及历史板测记录；当前 AI PC 控制器的实时旁路代码已加入；新版板端服务和完整旁路仍待现场验证，不能仅凭物理连接认定 FPGA 已参与当前任务。

## 已检查的软件

现有 `amd_hybrid_deploy_20261031/.venv` 中：`piper-sdk==0.6.2`、`python-can==4.6.1`、`opencv-python-headless==4.13.0.92`、ROCm PyTorch 2.11.0。上述 SDK、CAN 和 OpenCV 已成功导入。LeRobot 通过包内 `piper_towel_replica/runtime_source/src` 加入 `PYTHONPATH`，不要求单独安装同名 pip 包。系统中可找到 `ip`、`candump`、`ethtool`、`v4l2-ctl`，`can`、`can_raw`、`gs_usb`、`uvcvideo` 模块可用；最终选用的 USB-CAN 型号及内核绑定仍需现场识别。

ROCm 基线、XRT 和 NPU 视觉编码器已经在这台 AI PC 做过离线验证；Piper、相机、FPGA 的直连设备和完整实时控制尚未验证。不要为解决未出现的问题重复安装或覆盖现有环境。

## 接线和检测顺序

1. 两只机械臂分别使用各自 USB-CAN 适配器，三路相机接入 AI PC 的 USB 3 端口或外部供电的 USB 3 集线器；必要时将相机分布在不同主机控制器上。FPGA 可按实际固件使用 USB_UART 连接串口，或通过网口/网卡连接 PS_GE。Type-C 外形本身不能证明是高速数据接口。用户确认已有两个 USB-CAN 适配器和供电式扩展坞。
2. 用 `bash npu_deploy/run_showcase.sh` 选 2 查看本机依赖、CAN、video 节点和 USB 拓扑；选 3 诊断相机身份与格式。纯盘点不发运动命令；相机诊断会查询视频设备并保存一份本地盘点结果。输出为空或失败需要检查线缆、设备和权限，不自动判定缺驱动。
3. 用设备实际 USB 身份建立左右 CAN 与左/中/右相机角色映射。已有 `configs/cobot_site.json` 来自先前机器的设备标识：如果物理适配器/相机相同，可以复核后沿用；如果变更，则先准确确认左右身份，再用原有绑定工具更新该本地文件。不要仅按临时 `can0`/`video0` 编号推断物理左右。
4. 身份确认后，`./run.sh prepare --check-only` 可预览既有映射；实际 `./run.sh prepare` 会配置所识别 CAN 网络接口（必要时请求 sudo）并可接收反馈，不发机械臂动作。若设备身份未匹配，该工具会失败，需要先完成绑定。
5. 检查相机预览和三路 640×480/30fps 实际输出、CPU 占用、掉帧与 USB 链路速率。扩展坞只提供连接能力，是否满足多路相机要以并发测试为准。机型和设备 USB 2/3 模式可能不同。
6. 验证复位和原 ROCm hybrid 的只读链路，之后验证实验 NPU 视觉 hybrid。菜单的复位/hybrid 是 `--check-only` 命令预览；它们不表示硬件已经可用。现场动作前由操作人员根据设备状态和测试结果决定。
7. FPGA 确认板上实际状态与 overlay，启动已取得的 PYNQ 服务，先用已有合成客户端核对 UDP 往返、PS/PL 检查及数值；再对接 AI PC 控制循环的真实快照并测延迟与丢包。菜单中的 IP ping 只说明目标 IP 可达，不证明板卡身份或 FPGA 运算。

## 主要未决项

- 左右 USB-CAN 与三路相机的实际 USB 身份、端口和兼容驱动；扩展坞带宽及供电。
- ACT 同步动作块补充的控制周期尾延迟；CPU、ROCm 和 NPU 并发下的内存及稳定性。
- NPU 视觉路径与 ROCm baseline 的动作差异、guided 队列连续性和实机控制结果。
- FPGA 当前用于旁路动作规则检查、watchdog 和 LCD；确认板端运行版本及真实数据接入后的通信、数值和性能。当前没有 FPGA 视觉/模型加速。
- 本方案没有 Cobot 网络节点；此前两台主机协作设计及旧交接包是历史版本。

现场讲解顺序、板端合成演示准备和菜单用法见 `npu_deploy/SHOWCASE_GUIDE_ZH.md`。菜单选项 8 会向已运行的 FPGA 统一服务发送合成数据，结束时触发板端会话结束与回退，不控制机械臂。
