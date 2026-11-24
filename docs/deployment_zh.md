# FlexiFold 实机部署

本指南以仓库根目录为工作目录。现场版本使用两个 AgileX Piper、三路 RGB 相机、两条 USB-CAN 和通过以太网连接的 Zynq-7020 B35。当前日常流程使用 AMD Ryzen AI HX 370 的 Radeon 890M（`gfx1150`）、Python 3.12、ROCm 7.14.1 和 Ryzen AI 1.9。

## 配置本地权重

把 `FlexiFold_Runtime_Assets` 放到仓库旁边，在仓库根目录运行。

```bash
python3 tools/link_runtime_assets.py
```

若权重在其他位置，追加 `--source /path/to/FlexiFold_Runtime_Assets`。脚本会校验权重并把项目路径链接到原文件夹。参见 [运行资产](runtime_assets_zh.md)。

## 安装两套 Python 环境

1. 配置系统的 AMD GPU 驱动、XRT 和 NPU 驱动。以 [AMD ROCm 7.14.1 兼容性矩阵](https://rocm.docs.amd.com/en/docs-7.14.1/compatibility/compatibility-matrix.html)和 [Ryzen AI Linux 安装指南](https://ryzenai.docs.amd.com/en/latest/linux.html)为准。安装 CAN 与相机排查工具（`can-utils`、`v4l-utils`、`iproute2`）和桌面依赖（`python3-tk`、`fonts-noto-cjk`）。
2. 在 HX 370 创建主程序环境。

   ```bash
   bash amd_hybrid_deploy_20261031/tools/install_hx370_policy.sh
   ```

3. 按 AMD 安装程序说明将 Ryzen AI 1.9 装到 `$(pwd)/ryzen-ai-npu/venv`，使项目能找到 `venv/bin/python`、官方 quicktest 与 VitisAI provider。NPU 入口还使用 `/opt/xilinx/xrt/setup.sh`。两套环境放在各自的位置。原机版本记录见 [HX 370 迁移笔记](hx370_environment_migration_zh.md)。
4. 完成环境安装和权重配置后，运行 NPU 预检。

   ```bash
   bash npu_deploy/run_npu_preflight.sh
   ```

   如果锁定内存上限不足，按 NPU 预检的提示在同一终端调整。预检通过要求 NPU 子图和三路有效结果。

## 绑定现场硬件

- 使用 `bash amd_hybrid_deploy_20261031/run.sh bind --left-serial <LEFT> --right-serial <RIGHT>` 写入两条真实 USB-CAN 的序列号。每条必须与现场左、右机械臂物理对应。
- 用 `bash amd_hybrid_deploy_20261031/run.sh cameras --diagnose` 查 RGB 节点，用 `--preview-nodes <LEFT> <MIDDLE> <RIGHT>` 拍静态参考确认视角。确认后用 `bash amd_hybrid_deploy_20261031/run.sh cameras --camera-indices <LEFT> <MIDDLE> <RIGHT>` 保存相机绑定。尖括号均表示现场值。配置文件为 `amd_hybrid_deploy_20261031/piper_towel_replica/configs/cobot_site.json`。
- 将 AI PC 有线口设置为 `192.168.2.10/24`，PYNQ 为 `192.168.2.99/24`。按 [B35 板端步骤](b35_board_zh.md)安装当前板端包并登录。
- 同一套物理设备应由一个工作副本操作。在旧工作区切换到这个目录前，先退出原有任务和互动。

## 启动面板

```bash
bash npu_deploy/prepare_showcase.sh --panel
```

在面板完成「准备检查」，再进入「运行任务」。复位、折叠与失败后慢速归位均沿用面板确认。日常任务使用 FPGA FPG1 v3 旁路观察，ACT/SmolVLA 在 AI PC 生成控制命令。

摆位窗口入口如下。

```bash
./amd_hybrid_deploy_20261031/.venv/bin/python npu_deploy/towel_placement_preview.py
```

任务运行时，窗口可共用本任务录制器提供的中相机帧。首次使用可显示随仓库提供的默认轮廓。现场相机位置重新标定后，应使用本机成功记录更新参考。按 R 切换参考视图，按 Esc 关闭。

## 离线检查

```bash
bash npu_deploy/start_showcase.sh --check-only
bash npu_deploy/prepare_showcase.sh --check-only
./amd_hybrid_deploy_20261031/.venv/bin/python -m unittest discover -s npu_deploy/tests
(cd flexifold_booth_first_grasp && ../amd_hybrid_deploy_20261031/.venv/bin/python -m unittest discover -s tests)
python3 fpga_integration/protocol_fixtures/fpg1_v3_fve1_v1/check_vectors.py --help
```
