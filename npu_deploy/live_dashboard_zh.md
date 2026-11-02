# 任务实时界面

正式入口 `bash npu_deploy/start_showcase.sh` 在开始策略运行时自动打开界面。
窗口显示左、中央、右三路相机，以及左右臂当前六关节、夹爪和末端位姿。
先执行准备脚本并按现场提示确认；复位和正式运行会移动机械臂。

不连接任何硬件的模拟预览：

```bash
cd /run/media/zachary/program/FPGAcompition
./amd_hybrid_deploy_20261031/.venv/bin/python npu_deploy/demo_live_dashboard.py --seconds 30
```

模拟画面和数值均为程序生成，并非机械臂观测。窗口在 30 秒后自动退出；
关闭窗口只停止展示，实际任务需要在运行终端按 Ctrl+C 请求停止。

在正式任务未运行且设备已完成准备时，可以单独观察真实反馈：

```bash
cd /run/media/zachary/program/FPGAcompition
bash npu_deploy/run_dashboard_readonly.sh
```

该检查默认运行 20 秒，打开三路相机和两臂 CAN；Piper SDK 启动时会发送
设备信息查询，但不发送使能、复位或运动目标。它不加载模型、不使用 FPGA，
并使用 4 GiB 内存上限、2 核 CPU 配额的独立 scope。不要与正式任务同时运行，
以免争用设备；窗口关闭或 Ctrl+C 可提前退出。

关节值来自原控制器观察量，单位 rad；夹爪位置换算为 mm。末端 XYZ 和
RX/RY/RZ 来自 Piper SDK 已有 CAN 缓存，分别显示 mm 和度，坐标系以机械臂
SDK 反馈为准，并非经过双臂世界坐标系标定。位姿由多帧 CAN 信息组合；
三路相机、关节及末端反馈没有曝光级同步。界面根据 SDK 接收时间和界面
采样时间标出过期或未知数据，不应作为运动许可、安全保护或急停。

显示进程复用原任务的相机缓存和 SDK 接口，不额外打开相机或 CAN，不发送
控制消息。独立进程和单帧有界队列用于减少界面对控制的影响；目标采样为
5 Hz，并不能保证实时性。任务日志仍按原有方式记录；运行时的最终窗口状态
在每次 `*_task_recording/preview.json` 的 `preview` 字段中。

窗口需要可访问的图形桌面、Python Tk 支持及系统 Noto CJK 字体文件。中文
使用 Pillow 绘制，避免某些 Tk/X11 后端缺字。启动失败会在策略连接机械臂
之前报错，但正式脚本的复位阶段发生在此之前；因此应先用模拟预览检查。
该功能已做离线数据与界面逻辑测试，真实相机内容、位姿、延迟和运行时负载
仍需在现场验证。
