# SmolVLA NPU 实验

统一演示菜单：`bash npu_deploy/run_showcase.sh`。当前方案是将双臂、相机和 FPGA 直接连接 AMD AI PC；菜单可运行 AMD 演示、只读盘点本机设备、诊断相机、检查 FPGA IPv4 网络可达性，并预览本机复位与 hybrid 入口，不发送机械臂动作。FPGA ping 回复只能证明目标 IP 可达；设备绑定、FPGA 运算和实机控制仍需验证。

保留原交接包的 CPU/ROCm 入口。本目录只做离线模型导出、NPU 图像编码器实验和动作对照，不控制相机或机械臂。

`export_smol_vision.py` 加载 `smol030000` 全部训练权重，以原前处理为三张固定图片生成 CPU 参考，再导出固定输入 `[1,3,512,512]` 的视觉编码器与 connector。导出中固定的全有效 patch mask、位置索引和输入大小仅适用于当前项目配置；导出脚本逐张检查它与原始前向的差异。输出 `[1,64,960]`，并记录权重、配置和 ONNX 哈希。

`check_smol_vision.py` 使用 Ryzen AI 环境分别运行 ONNX CPU 或 Vitis AI EP，保存图像特征误差、执行时间和输出。Vitis AI provider 已注册、或日志中的算子分配，并不足以单独证明运行成功；需要完整推理正常结束并检查输出。首次会编译模型，可能耗时较长。

在宿主机运行 NPU 测试（需要项目目录可写、已安装的 Ryzen AI 1.9 和 XRT 2.26）：

```bash
sudo prlimit --pid $$ --memlock=1073741824:1073741824
bash /run/media/zachary/program/FPGAcompition/npu_deploy/run_npu_check.sh
```

`compare_smol_policy.py` 用独立 worker 环境运行 ONNX 视觉编码器，对照原始 SmolVLA 的普通和 guided 输出；父进程保留原 PyTorch，包括 RTC 自动求导。脚本中的 `cpu/cpu` 可不访问 GPU/NPU 进行通信及动作对照。宿主机可以运行 ROCm/NPU 对照：

```bash
sudo prlimit --pid $$ --memlock=1073741824:1073741824
bash /run/media/zachary/program/FPGAcompition/npu_deploy/run_policy_check_bounded.sh
```

该入口使用临时 systemd 用户 scope，将本次测试的 CPU 配额设为 4 核、内存设为 12 GiB、swap 设为 0；设备驱动使用的共享内存未必全部计入。默认一次观测、一次测量，首次编译可能耗时数分钟。`memlock` 为允许锁定内存的上限，并非预先分配的内存；上面的命令将当前终端及其子进程的上限设为 1 GiB。

宿主机固定样例对照已成功：日志显示 NPU 子图实际执行，原路径与 NPU 视觉路径均执行 10 次 guided 自动求导。一次 guided 对照的最大关节差异为 0.0234 rad，左右夹爪最大差异分别约 0.284 mm 和 0.491 mm。结果位于 `artifacts/smol_vision/policy_rocm_npu_first.json`；这是一份固定观测，不代表闭环精度或真实性能。

同一观测预热后各重复 3 次，guided 路径原 ROCm 为 1.157–1.184 s，NPU 视觉与 ROCm 混合为 0.745–0.751 s，结果位于 `artifacts/smol_vision/policy_rocm_npu.json`。首次 NPU 编译启动约 166 s，随后使用缓存的启动约 2.1 s；计时不包含启动过程。重复的是同一观测，不是多场景验证。

`check_hybrid_offline.py` 同时加载项目 ACT 和通过实验入口加载的 SmolVLA，以同一固定观测调用 guided 推理和项目的 blend 函数。它以刚算出的命令代替下一步的反馈，按固定动作块回放，不模拟异步队列或机械臂。使用独立限额入口运行：

```bash
sudo prlimit --pid $$ --memlock=1073741824:1073741824
bash /run/media/zachary/program/FPGAcompition/npu_deploy/run_hybrid_offline_check.sh
```

首次 20 步回放在两条路径均完成，1 步的融合来源不同：第 20 步 NPU 路径的滤波后步长为 0.100425 rad，超过原有 0.10 rad 判定值，导致该步回退到 ACT。最大目标关节差异约 0.01894 rad；两侧最终目标夹爪没有差异。见 `artifacts/smol_vision/hybrid_offline.json`。这些数字不代表实际机械臂上的触发率。

同一组观测扩展至 50 步后，两条路径均完成，融合来源在第 20、22 步不同，均由 NPU 路径的滤波后步长超过 0.10 rad 引起；最大目标关节差异为 0.02881 rad，夹爪目标无差异。两条路径各自的融合判断在这次理想化回放中分别有 37 和 39 步未接受 SmolVLA；不能把该比例当成真实运行的触发率。逐步结果见 `artifacts/smol_vision/hybrid_offline_50.json`。

`raw_data/episode_9.hdf5` 为用户提供的原始 900 帧记录，包含三路图像、14 维 qpos、effort 与 action；未发现独立时间戳或明确单位标注。`extract_episode_fixtures.py` 仅按帧索引读取并提取第 0、450、899 帧到 `artifacts/episode9/fixtures/`。图像角色及 14 维排列暂按字段名称和项目约定推测，数值未作单位转换；三帧的图像与状态已逐项核对一致。它们适合用于相同输入下的两后端离线对照，不证明原始数据的物理约定、训练来源或时间同步精度。通过 `run_episode_check.sh` 可在原有资源限制下依次测试三帧，逐帧结果保存在 `artifacts/smol_vision/episode9_frame_*.json`。

三帧 guided 对照均已完成，日志均显示 NPU 实际执行；两路径每帧均调用 10 次 guided 自动求导。第 0、450、899 帧的最大关节输出差异分别为 0.01905、0.01717、0.01493 rad，三帧中最大夹爪输出差异为 0.2115 mm。每帧仅测量一次，且未预热；不可据此推断稳定时延或数据集整体精度。

`run_episode_hybrid_check.sh` 在相同资源限制下，逐帧同时加载 ACT 与通过实验入口加载的 NPU 视觉模型，比较项目的原始融合函数生成的第一个目标。每帧独立运行，RTC 前缀由该帧生成，不能当作完整时序闭环。报告保存在 `artifacts/episode9/hybrid_frame_*.json`。

FastFlowLM 官方包另提供完整 SmolVLA 的 NPU 前向推理：下载包 SHA-256 已对照 Hugging Face 官方元数据。`prepare_fastflow_model.py` 比较官方模型和自训练模型的全部键、形状及网络参数，保留自己的 28 维状态和 14 维动作配置，加入官方运行时所需的视觉与专家结构元数据，并使用指向原权重的链接，不修改原文件。`prepare_fastflow_reference.py` 使用项目自己的状态、三路图片适配、token、归一化和固定噪声生成参考。

`prepare_fastflow_bf16.py` 为 FastFlowLM 单独创建 BF16 权重副本，以满足运行时的权重格式要求；原始训练权重保持不变。宿主机一次固定输入测试已完成：预热后 3 次前向约 0.426–0.434 秒，动作对照结果保存在 `artifacts/fastflow_custom_bf16/fastflow_check.json`。这还不能代表 guided RTC 或实际控制表现。

```bash
sudo prlimit --pid $$ --memlock=1073741824:1073741824
bash /run/media/zachary/program/FPGAcompition/npu_deploy/run_fastflow_check.sh
```

此项比较的是相同输入和噪声下的完整非 guided 动作块；FastFlowLM 接口没有提供项目的 guided RTC 自动求导，不能凭这个测试声称完整混合策略已迁移。模型比较结果包含每个动作维度的误差，包含夹爪与各关节不同的物理单位。

结果及日志保存在 `artifacts/smol_vision/`。`run_policy_check.sh` 测试一份固定观测，不应将其 p95 当成长时间真实性能；它不会启动控制器。NPU 进程使用 `GLIBC_TUNABLES=glibc.rtld.execstack=2`、XDNA 预加载以及独立 `LD_LIBRARY_PATH`；这些设置局限在该进程，完整 ROCm 项目路径不依赖它们。

已准备独立的实验控制入口 `run_hybrid_npu.sh`，会使用原 hybrid 的参数和控制逻辑，通过独立进程替换 SmolVLA 图像编码器。其 `--check-only` 已检查参数，上述离线对照也验证了模型编译和一次动作比较；完整控制入口及设备运行尚未验证：

```bash
bash /run/media/zachary/program/FPGAcompition/npu_deploy/run_hybrid_npu.sh --check-only
```
