# Pre-run readiness

准备日期：2026-11-13。目标是补齐当前 HX370 真机运行链路的本机准备；所有验证均不向机械臂发送动作。

当前机器记录以 [latest.json](bimanual-towel-folding-ryzen-zynq/npu_deploy/artifacts/readiness/latest.json) 为准。本机软件与外设分别检查；`LOCAL_SOFTWARE_READY=true` 不表示机械臂已获准运动，只有现场设备核验后才能讨论真机执行。硬件未接入时，默认检查返回 2 是待现场核验的状态。

## Local setup

模型已通过相对符号链接接入运行项目：

- ACT：`amd_hybrid_deploy_20261031/piper_towel_replica/models/act050000/`。
- SmolVLA：`amd_hybrid_deploy_20261031/piper_towel_replica/models/smol030000/`。
- 本地基础模型：`amd_hybrid_deploy_20261031/assets/smolvlm2_500m/`。
- NPU ONNX：`npu_deploy/artifacts/smol_vision/smol_vision.onnx`，原导出身份记录保留。

已补齐官方 ResNet18 初始化缓存、SmolVLM 所需 num2words/docopt、基础串口依赖 pyserial；新增依赖已写入 common.lock.txt。原始模型参数、归一化和 tokenizer 未修改。

CPU 和 ROCm 严格加载及合成输入前向推理结果：[CPU](local_environment_record/models-deployment-cpu.json)、[ROCm](local_environment_record/models-deployment-rocm.json)。ROCm 检查使用正式任务的 precision=auto。只验证加载与有限输出，不代表真实毛巾场景精度。

本机生成的三路图像/状态/原生 PyTorch 参考位于 `npu_deploy/artifacts/synthetic_preflight/`，`source_kind=SYNTHETIC`。现有离线检查默认 fixtures 与 NPU fixture.npz 已指向这组数据，metadata 保存参考来源和权重身份。原始现场参考仍未交接，不能将合成输入当作现场录像或标定证据。

NPU 编译缓存位于 `npu_deploy/artifacts/npu_cache/`，预检和正式 vision_worker 共用同一设置；模型 SHA、运行时、驱动和 XRT 身份变化会选择新的缓存目录。选项依据 [AMD Ryzen AI 1.9 文档](https://ryzenai.docs.amd.com/en/latest/modelrun.html#vitisai-ep-cache)。缓存首次建立和复用结果见实际预检日志。

## Performance envelope

系统当前处于 performance 模式，AMD CPU Boost 已开启。主要推理和验证入口共用 `npu_deploy/runtime_resources.sh`：本机默认允许全部24个逻辑核，内存上限22 GiB，为桌面和系统保留约5 GiB；swap为0，避免推理因换页长时间卡住。CPU辅助算子的默认线程数为8，不将整个进程限制为8核。可用 AIPC_HYBRID_MEMORY_MAX、AIPC_HYBRID_CPU_QUOTA、AIPC_HYBRID_THREADS 调整。

NPU 的BF16计算格式与算力限制不同。当前分工为ACT/SmolVLA其余部分使用ROCm，SmolVLA图像编码器使用NPU。新测试会记录低精度路径相对原生路径的实际差异，不能只凭TOPS或没有崩溃就宣称现场动作等价。

NPU独立power mode已在用户执行官方设置命令后读回为performance，证据见 local_environment_record/npu-platform-performance.json。未使用超频或手动电压设置。重启/驱动重载后可重新读回检查模式；当前设置不冒充跨重启持久化配置。

完整合成回放首次20步均完成；原生/NPU路径有1步分支差异，最大关节目标差约0.02345 rad。记录位于 hybrid_offline.json；这是单次合成输入对比，尚不是实机精度或稳定性承诺。

## Read-only checks

在运行项目目录执行：

```bash
cd /home/ysyx/FPGA_competition/bimanual-towel-folding-ryzen-zynq
bash npu_deploy/check_before_hardware.sh --software-only
```

这条命令检查部署文件、依赖、模型验证身份、ROCm、NPU 预检记录、memlock 和外设枚举；不会使能、复位或移动机械臂。它不会自动重新运行耗时模型测试。

已接入的 B35 板卡可额外检查 Jupyter 管理状态和 FPG1 v3 诊断探针：

```bash
bash npu_deploy/check_before_hardware.sh --probe-fpga
```

探针只发送诊断和 status 请求，不发送 start/finish 或机器人动作。后续正式脚本仍会在自己的会话内重做 PL 探针。

## Field connection

最新检查时没有 `/dev/video*` 和两臂 SocketCAN 接口。尚需接入三路摄像头、两臂 USB-CAN 与机械臂电源，核验站点中的实际 USB/相机身份；保留身份绑定，不用固定 video 序号替代身份核验。

Zynq-7020 已通过网线连接并实测。主机 `enp195s0=192.168.2.10/24`，板卡 `192.168.2.99`，NetworkManager 配置 `fpga-zynq7020` 自动连接且不接管 Wi-Fi 默认路由。B35 已修订、重建并安装到独立版本目录，通过用户登录的 Jupyter 管理，不要求迁移旧生命周期密钥。原 B32 项目和旧管理器保留。

当前 B35 板端正常来源为 `host_observation`，已启用 LCD；主机 FUI1 状态应答和 FPG1 v3 真实 PL 诊断通过。屏幕帧缓冲和扫描计数已核验；用户已对编号1989的实体LCD检查画面确认方向、边框、四角文字、颜色及无明显闪烁。板端系统日期已同步至主机，时区为Asia/Shanghai，重新认证后读回通过。管理命令和 Notebook 见 [board README](bimanual-towel-folding-ryzen-zynq/npu_deploy/fpga_board/README_zh.md)。

## Field start

设备接好并核验现场条件后，先运行准备界面：

```bash
bash npu_deploy/prepare_showcase.sh --panel
```

准备流程识别外设、接收反馈、检查 FPGA 服务和 NPU；不使能机械臂。需要配置 SocketCAN 时可能由原流程请求 sudo。

现场准备通过后，使用正式启动界面：

```bash
bash npu_deploy/start_showcase.sh --panel
```

正式流程保留分阶段 Enter 确认，复位和任务阶段会实际使能并移动机械臂。UI 不可用时回退终端。AMD CPU、ROCm 和 NPU 证据仍保留在流程中。

## FPGA upgrade boundary

当前明确选择 B35（BUILD_ID 0x46504735），协议为 FPG1 v3 / FVE1 v1。已安装 Vivado 2018.3，修订并重新生成 bit/HWH，完成时序门槛、真实 PL 和 UDP 合成回归。B34、B32 历史版本保留，不自动降级。

反馈过期、相邻帧间隔、寄存器快照位宽三个 RTL 修订已在 B35 落实；完整配置 CRC/SHA、生成常量、PS 启动校验和 PC 两端身份核验均已实现并验证。当前配置资格为 synthetic_test，主机保持阶段观测，`quality_profile=null`，HOST 模式拒绝有效视觉特征。真实相机门限标定和完整现场验收仍待完成。详见 [B35 release report](bimanual-towel-folding-ryzen-zynq/docs/FPGA_B35_RELEASE_zh.md)。

## Final verification record

最终只读检查：LOCAL_SOFTWARE_READY=true；FPGA 板端诊断通过；HARDWARE_READY=false；READY_FOR_MOTION=false。仍需摄像头、两臂 CAN、设备身份核验和现场标定验收，不能通过合成测试替代。

- ACT / SmolVLA：CPU 与 ROCm 严格加载、保存的预/后处理及前向推理通过。
- NPU：真实执行profile确认VitisAI内核，持久缓存命中，三路有效输出；performance模式下每路编码约0.126–0.129秒。
- 性能稳定性：全程performance模式、24逻辑核可用、22GiB内存上限下完成100次NPU引导推理，每次进行20步无动作回放。中位0.651秒，P95约0.661秒；这不是机械臂单步控制耗时。
- 主进程RSS约2.67GB，100次末尾仅比首轮高8KiB；PyTorch allocated/reserved分别约1.22/2.02GB，首尾一致。上述值不等于整个驱动/NPU/桌面的总内存。
- 主项目相关回归：68项通过；交接附带 PC 测试8项通过；旧生命周期回归6项通过。另有B35 RTL八项、配置校验八项通过。测试范围和现场剩余条件见B35发布报告。

证据：[性能稳定性摘要](local_environment_record/performance_stability_summary.json)、[就绪记录](bimanual-towel-folding-ryzen-zynq/npu_deploy/artifacts/readiness/latest.json)、[完整混合回放](bimanual-towel-folding-ryzen-zynq/npu_deploy/artifacts/smol_vision/hybrid_offline.json)。

后续修改模型或预处理后可使用 npu_deploy/check_deployed_models.py 重新检查；更换模型、运行时或驱动后会选择新的NPU编译缓存。已完成 B35 修订发布与板端验证；当前没有执行机械臂使能、复位或 CAN 动作发送。

## Cooperative display

B35 LCD 与电脑协作页已改为真实消息／PL 结果驱动，说明见 [协作界面](bimanual-towel-folding-ryzen-zynq/docs/FPGA_COOPERATIVE_VISUAL_zh.md)。同一任务编号串联电脑送达、FPGA 检查和板屏结果；无数据及断连时明确显示等待／历史。合成联测通过不代表机械臂与相机现场任务完成，最终字体在实体屏上的可读性仍待目视确认。
