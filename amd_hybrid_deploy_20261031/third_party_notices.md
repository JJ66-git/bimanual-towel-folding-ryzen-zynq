# 随包第三方文件

- LeRobot 源码：保留 `piper_towel_replica/runtime_source/LICENSE` 与源码文件头；该目录包含原项目本地调整。
- act_reference：保留 `piper_towel_replica/upstream/act_reference/LICENSE`、`NOTICE`、`CITATION.cff`。
- SmolVLM2 底座：保留 `assets/smolvlm2_500m/README.md` 模型卡，标注 Apache-2.0；底座来源和 revision 见模型文档。训练后的 ACT/Smol 检查点来自用户现有项目，本包不新增其权属声明。
- ResNet18 初始化缓存来自 torchvision 的预训练模型；随包只保留本入口构造模型所需缓存。torchvision / PyTorch / Piper SDK / Python 依赖在新机从其官方渠道安装并遵循各自许可。
- AMD ROCm、Ryzen AI 和系统驱动安装文件未重新分发，使用安装说明指向的官方渠道与条款。

本包保留第三方许可，不为上游文件或用户模型重新指定统一许可证。
