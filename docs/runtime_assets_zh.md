# 运行资产

本机的独立文件夹 `FlexiFold_Runtime_Assets` 包含运行所需的模型和配套文件，约 3.4 GiB。换机时请整目录复制。Git 仓库提供配置脚本、校验清单、FPGA 材料和合成预检输入。

## 拉取仓库后配置

把权重文件夹放在仓库旁边，然后在仓库根目录用 Python 3.11 或更新版本运行。

```bash
python3 tools/link_runtime_assets.py
```

文件夹存放在其他位置时显式指定路径。

```bash
python3 tools/link_runtime_assets.py --source /path/to/FlexiFold_Runtime_Assets
```

脚本先验证每个模型和配套文件的 SHA-256，再创建五个本地符号链接，保留已校验通过的现有资产。加 `--check-only` 只校验。移动权重文件夹后需要重建指向旧位置的链接。Git 忽略这些运行路径，执行配置时无需下载模型。

| 独立文件夹中的路径 | 项目运行路径 |
| --- | --- |
| `checkpoints/act050000/` | `amd_hybrid_deploy_20261031/piper_towel_replica/models/act050000/` |
| `checkpoints/smol030000/` | `amd_hybrid_deploy_20261031/piper_towel_replica/models/smol030000/` |
| `vision/smolvlm2_500m/` | `amd_hybrid_deploy_20261031/assets/smolvlm2_500m/` |
| `vision/smol_vision.onnx` | `npu_deploy/artifacts/smol_vision/smol_vision.onnx` |
| `torch/` | `amd_hybrid_deploy_20261031/piper_towel_replica/deployment_assets/torch/` |

从仓库根目录运行 `sha256sum -c docs/runtime_assets.sha256` 可同时检查模型、合成预检输入及摆位参考。从权重文件夹内运行 `sha256sum -c SHA256SUMS` 可单独检查权重包。上游许可证见 [第三方声明](../amd_hybrid_deploy_20261031/third_party_notices.md)。安装 ROCm、Ryzen AI 和绑定硬件见 [实机部署](deployment_zh.md)。
