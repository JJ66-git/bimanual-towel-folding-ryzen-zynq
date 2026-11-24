# B35 protocol revision

本修订保留 FPG1 v3 / FVE1 v1 的头、payload、CRC 算法、长度、状态值和查询语义。修订 BUILD_ID 为 `0x46504735`，接收端严格匹配所选发布版本；原 B34 固定报文夹具继续作为旧版本格式证据保存，不改写原始包。

视觉本笔快照恢复规范布局：done=bit31，count=[23:16]，reason=[15:8]，state=[3:0]；当前状态寄存器 0x0e0 的布局保持原规范。

0x098 返回规范 JSON 的 CRC32，复位后仍保持；写入返回 SLVERR。发布清单同时保存规范 JSON SHA256、实际 bit/HWH SHA256、源码文件 SHA256、目标器件和工具版本。RTL 参数从该 JSON 生成；PS 先校验 JSON 资格、guard 配置身份、BUILD_ID 和配置CRC，再接受服务请求。

已资格化配置仅为 synthetic_test。主机正常观测模式不接受 feature_flags=1 的视觉特征，防止合成门限产生 LIVE 成功结论；阶段只读观测保留。真实视觉标定必须单独交付对应算法、配置及成功/失败标注证据，并重新构建匹配位流与软件。
