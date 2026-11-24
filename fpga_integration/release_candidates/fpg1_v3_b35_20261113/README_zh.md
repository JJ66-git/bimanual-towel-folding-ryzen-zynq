# B35 accepted synthetic revision

源自 B34 的独立修订候选，BUILD_ID `0x46504735`。当前目录保留可重建源码，构建中间文件在 `validation_runs/`，位流和报告在 `overlay/`。已完成布线门槛、真实PL及UDP合成回归，摘要见 acceptance/release_acceptance.json，详细证据以工作区 `local_environment_record/fpga_revision_release_20261113/` 的最终记录为准。

修复断流过期、相邻帧间隔和 32 位结果快照；JSON 配置按协议第12节规范化，生成 `rtl/fold_verify_params.svh` 和配置 CRC。RTL 阈值直接引用生成常量，PS 启动匹配配置、guard 身份与 PL `0x098`；B35 不能使用 B34 的快照解码。线上 FPG1/FVE1 布局和协议版本不变，BUILD_ID 严格区别。

当前 `calibration_status=synthetic_test`，使用合成特征向量，不包含真实图像 HSV/ROI 标定证据。HOST 模式拒绝有效视觉特征，只观测阶段；任何配置修改均需重新生成常量、重新构建并重新验收。不能将状态改为 field_validated 就宣称完成现场标定。

在本目录执行：

```bash
python3 scripts/generate_fold_config.py --check
python3 -m unittest discover -s tests -v
python3 scripts/run_revision_tests.py --out /tmp/b35_rtl_validation
/home/ysyx/FPGA_competition/tools/xilinx/Vivado/2018.3/bin/vivado -mode batch -source build_pynq_lcd_overlay.tcl
```

构建目录必须全新，禁止覆盖旧证据。重复构建使用新的 `FPGA_BUILD_TAG`，并同步部署路径。完成后运行 `scripts/verify_build.tcl` 核验实际管脚、RGB 三态输出、像素时钟和布线后 setup/hold。真实 PL 专项回归见 `validation/validate_revision.py`，不访问摄像头或机械臂。
