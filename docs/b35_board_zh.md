# B35 板端部署

当前配置为 FPG1 v3 / FVE1，BUILD_ID `0x46504735`，目标器件 `xc7z020clg400-2`。位流与 HWH、板端 Python、RTL、Vivado Tcl、LCD 字体及协议测试都随源码提供。

1. 上传 [b35_pc_update.zip](../npu_deploy/artifacts/fpga_board/releases/b35_pc_update.zip) 和 [B35 Notebook](../npu_deploy/fpga_board/notebooks/02_inspect_and_validate_b35.ipynb) 到板端 Jupyter 的同一目录。
2. 按 Notebook 逐步检查包和板卡，按现场需要启用安装。安装目录为 `/home/xilinx/jupyter_notebooks/fpga_fpg1_v3_b35_20261113`。已安装的不同版本应先保留可回退副本。重建包可在仓库根目录运行 `python3 npu_deploy/fpga_board/build_revision_release.py --name b35_pc_update`。
3. 在 AI PC 登录板卡，确认服务状态。

   ```bash
   ./amd_hybrid_deploy_20261031/.venv/bin/python npu_deploy/fpga_board/jupyter_client.py --login
   ./amd_hybrid_deploy_20261031/.venv/bin/python npu_deploy/fpga_board/control_jupyter.py status
   ```

源码在 [B35 发布目录](../fpga_integration/release_candidates/fpg1_v3_b35_20261113)。运行准备面板会检查并启动观测服务。GPIO、LCD 和机械臂接线按现场设备核验。
