# 双臂叠毛巾项目：原环境记录与 HX 370 新机迁移指南

**整理日期：2026-11-12**  
**目标机器：AMD Ryzen AI 9 HX 370，32 GB 内存；Ubuntu 24.04 已安装；整机型号、固态硬盘容量及是否有独立显卡待确认。**  
**设备与模型：沿用原双 Piper、三路相机、Zynq 板和原先训练的参数。**

本文记录原项目已有的现场配置，并给出新电脑的安装和验收路线。原机记录不等于新机已验证；新机的实际安装结果与版本填在文末。本文不要求重新训练模型。

## 0. Ubuntu 已安装：按顺序执行的详细安装手册

以下命令都在新 AI PC 的 Ubuntu 终端执行。每个代码块完成后先检查输出，再进入下一步。按 Ctrl+Alt+T 打开终端；输入 sudo 密码时屏幕不显示字符，输入后按回车即可。本机尚未远程连接，这些命令尚未在你的 HX 370 上跑通。

**按第 0 节的顺序实际操作。**第 1～8 节保留原机环境、迁移背景和参考信息；其中与第 0 节重复的创建项目、创建环境命令不需要执行第二遍。

目标为 Ubuntu 24.04、Python 3.12、ROCm 7.14.1、PyTorch 2.11.0 和 Ryzen AI 1.9 / XRT 2.26.89。旧机是 `gfx1152`，HX 370 是 `gfx1150`；需要按新架构安装软件，不能直接复制旧虚拟环境。

### 0.1 检查系统、内核与可用空间

```bash
cat /etc/os-release
uname -r
lscpu | grep -E 'Model name|CPU\(s\)'
free -h
df -h /
```

确认 Ubuntu 24.04、Ryzen AI 9 HX 370 和约 32 GB 内存。保存上述输出。AMD [ROCm 7.14.1 兼容性矩阵](https://rocm.docs.amd.com/en/docs-7.14.1/compatibility/compatibility-matrix.html)的 Ryzen 部分列出 Ubuntu 24.04.4 / **OEM 6.17**；Ryzen AI 1.9 Linux 文档要求 Ubuntu 24.04、内核至少 6.10。若当前内核与矩阵不一致，先记录结果和整机型号，按 AMD 与整机厂商的说明处理；不要假定普通 HWE 内核等同于 OEM 内核，也不要混装不同版本的驱动。

AMD Ryzen AI 1.9 安装包约 12.9 GB，解压和安装还需要额外空间。建议在下载目录和系统盘留出数十 GB，实际需要量还包括模型和依赖。驱动版本确定前，先不进行大规模系统升级。

### 0.2 将 Ubuntu 普通软件源换成中科大镜像

Ubuntu 24.04 通常使用 `/etc/apt/sources.list.d/ubuntu.sources`。先查看和备份：

```bash
ls -l /etc/apt/sources.list.d/ubuntu.sources
cat /etc/apt/sources.list.d/ubuntu.sources
sudo cp -a /etc/apt/sources.list.d/ubuntu.sources \
  /etc/apt/sources.list.d/ubuntu.sources.before-ustc
```

如果第一行提示文件不存在，先查看 `/etc/apt/sources.list` 和 `/etc/apt/sources.list.d/`，不要运行下面的替换。对于存在的标准配置，仅替换 Ubuntu 普通 archive 地址，保留官方安全更新源和其他第三方源：

```bash
sudo sed -i -E \
  's@https?://([[:alnum:]-]+\.)?archive\.ubuntu\.com/ubuntu/?@https://mirrors.ustc.edu.cn/ubuntu/@g' \
  /etc/apt/sources.list.d/ubuntu.sources

cat /etc/apt/sources.list.d/ubuntu.sources
sudo apt update
```

确认普通软件源指向 `mirrors.ustc.edu.cn`，安全更新保留 Ubuntu 官方地址。若出现源签名或下载错误，先排查。恢复原配置的方法：

```bash
sudo cp -a /etc/apt/sources.list.d/ubuntu.sources.before-ustc \
  /etc/apt/sources.list.d/ubuntu.sources
sudo apt update
```

若文件原本使用其他镜像，上述替换可能无变化；参考[中科大 Ubuntu 24.04 配置说明](https://mirrors.ustc.edu.cn/help/ubuntu.html)，只修改普通 Ubuntu 源的 `URIs`，保留 `Suites`、`Components`、`Signed-By` 和独立安全源，不要新增重复源。

### 0.3 安装基础工具，设置 pip 中科大镜像

```bash
sudo apt install -y git curl wget ca-certificates unzip \
  python3.12 python3.12-venv python3-pip \
  pciutils usbutils v4l-utils can-utils iproute2 \
  build-essential pkg-config dkms libboost-filesystem1.74.0 \
  fonts-noto-cjk
```

`fonts-noto-cjk` 用于项目中文界面；`v4l-utils`、`can-utils` 用于检查相机和 CAN。

```bash
python3 -m pip config --user set global.index-url https://mirrors.ustc.edu.cn/pypi/simple
python3 -m pip config list
```

普通 Python 包走中科大镜像。AMD 定制的 ROCm、PyTorch、torchvision 包使用 AMD 官方索引；NPU 安装包也从 AMD 官方下载。不要安装普通 CPU 版 PyTorch 后误以为已完成 GPU 部署。

### 0.4 下载项目并创建主环境

示例路径为 `~/robot/bimanual-towel-folding-ryzen-zynq`。如果目录已经存在，先检查，不要覆盖。

```bash
mkdir -p ~/robot
cd ~/robot
git clone https://github.com/JJ66-git/bimanual-towel-folding-ryzen-zynq.git
cd ~/robot/bimanual-towel-folding-ryzen-zynq
pwd
python3.12 --version
python3.12 -m venv amd_hybrid_deploy_20261031/.venv
```

若 GitHub 下载失败，记录报错；也可在另一台电脑下载代码后用 U 盘复制。主虚拟环境和后面的 NPU 环境是两套独立环境。

```bash
amd_hybrid_deploy_20261031/.venv/bin/python -m pip install \
  -i https://mirrors.ustc.edu.cn/pypi/simple \
  'pip==25.3' 'setuptools==80.9.0' 'wheel==0.45.1'
```

原 `tools/install_env.sh rocm` 写死 `gfx1152`，不要直接运行。下面按原版本安装 HX 370 的 `gfx1150` 包；AMD 索引可查到这些 Python 3.12 Linux wheel，但新机运行结果尚待实测：

```bash
amd_hybrid_deploy_20261031/.venv/bin/python -m pip install \
  --index-url https://repo.amd.com/rocm/whl-multi-arch/ \
  'rocm[libraries,device-gfx1150]==7.14.1' \
  'torch[device-gfx1150]==2.11.0+rocm7.14.1' \
  'torchvision[device-gfx1150]==0.26.0+rocm7.14.1'
```

可能下载较大的软件包。若出现找不到 wheel、连接失败或依赖冲突，保留报错，不要改装无版本约束的 CPU 版。再从中科大镜像安装原项目锁定依赖：

```bash
amd_hybrid_deploy_20261031/.venv/bin/python -m pip install \
  -i https://mirrors.ustc.edu.cn/pypi/simple \
  -r amd_hybrid_deploy_20261031/requirements/common.lock.txt \
  -c amd_hybrid_deploy_20261031/requirements/rocm.constraints.txt

amd_hybrid_deploy_20261031/.venv/bin/python -m pip check
```

期望 `No broken requirements found`。镜像若缺固定版本或解析器要求更换 ROCm 包，保留错误，不要直接移除约束。

### 0.5 开启 GPU 权限并测试 PyTorch

```bash
sudo usermod -a -G render,video "$USER"
```

**注销再登录或重启**后重新打开终端：

```bash
cd ~/robot/bimanual-towel-folding-ryzen-zynq
groups
lspci -nn | grep -Ei 'vga|display|3d|amd|nvidia'
ls -l /dev/kfd /dev/dri/renderD* 2>/dev/null
amd_hybrid_deploy_20261031/.venv/bin/python - <<'PY'
import torch, torchvision
print('torch:', torch.__version__)
print('torchvision:', torchvision.__version__)
print('HIP:', torch.version.hip)
print('GPU available:', torch.cuda.is_available())
if torch.cuda.is_available():
    print('GPU:', torch.cuda.get_device_name(0))
    print('architecture:', getattr(torch.cuda.get_device_properties(0), 'gcnArchName', 'unknown'))
    a = torch.randn((512, 512), device='cuda')
    b = a @ a
    torch.cuda.synchronize()
    print('matmul finite:', bool(torch.isfinite(b).all()))
PY
```

期望 HIP 非空、GPU available 为 True、GPU 名称为 AMD 890M、architecture 包含 gfx1150、matmul finite 为 True。ROCm 版 PyTorch 的接口名仍为 `torch.cuda`。如另有独显，设备 0 未必是 890M；若结果不符，先核对内核、设备节点、组权限和 wheel。

```bash
cd ~/robot/bimanual-towel-folding-ryzen-zynq/amd_hybrid_deploy_20261031
bash run.sh doctor --backend rocm
```

期望 `basic_backend_check_passed: true`。这只说明基础张量计算通过，不代表 NPU 或模型已经运行。

### 0.6 安装 AMD XRT 和 NPU 驱动

先确认 0.1 节的内核与 AMD 指南匹配。下列文件名来自 [AMD Ryzen AI 1.9 Linux 官方指南](https://ryzenai.docs.amd.com/en/latest/linux.html)。安装会修改系统驱动和 DKMS；如果包内容或内核与官方要求不一致，先停下核对。

```bash
mkdir -p ~/Downloads/ryzen-ai-1.9-npu
cd ~/Downloads/ryzen-ai-1.9-npu
wget -c https://download.amd.com/opendownload/RyzenAI/1.9.0/RAI_1.9_Linux_NPU_XRT.zip
unzip -t RAI_1.9_Linux_NPU_XRT.zip
unzip -q RAI_1.9_Linux_NPU_XRT.zip -d packages
find packages -type f -name '*.deb' | sort
```

确认列出下方四个文件。解压目录可能有子目录，命令逐项查找唯一文件；找不到或出现多个同名文件时停止：

```bash
for pkg in \
  'xrt_202620.2.26.89_24.04-amd64-base.deb' \
  'xrt_202620.2.26.89_24.04-amd64-base-dev.deb' \
  'xrt_202620.2.26.89_24.04-amd64-npu.deb' \
  'xrt_plugin.2.26.260102.59.release_24.04-amd64-amdxdna.deb'; do
  mapfile -d '' -t found < <(find "$PWD/packages" -type f -name "$pkg" -print0)
  if [ "${#found[@]}" -ne 1 ]; then
    printf '无法唯一定位：%s（找到 %s 个）\n' "$pkg" "${#found[@]}"
    break
  fi
  printf '安装：%s\n' "${found[0]}"
  sudo apt install --fix-broken -y "${found[0]}" || break
done
```

确认四个包全部成功；若 Secure Boot、内核模块编译或依赖报错，记录完整输出，不要直接关闭安全设置绕过。安装后建议重启，再检查：

```bash
source /opt/xilinx/xrt/setup.sh
xrt-smi examine
dkms status
```

`xrt-smi examine` 应列出 NPU；名称和 BDF 不必与旧机相同。没有设备时先检查内核、驱动和 DKMS。

### 0.7 安装 Ryzen AI NPU 软件及独立环境

AMD 官方安装包约 **12.9 GB**，确认磁盘空间和网络。版本与下载信息见 [Ryzen AI 1.9 Linux 安装指南](https://ryzenai.docs.amd.com/en/latest/linux.html)。

```bash
mkdir -p ~/Downloads/ryzen-ai-1.9-runtime
cd ~/Downloads/ryzen-ai-1.9-runtime
wget -c https://download.amd.com/opendownload/RyzenAI/1.9.0/ryzen_ai-1.9.0.tgz
tar -xzf ryzen_ai-1.9.0.tgz
find "$PWD" -type f -name install_ryzen_ai.sh -print
```

根据输出进入包含 `install_ryzen_ai.sh` 的目录。如果脚本就在当前目录，可直接执行下方命令。先阅读 AMD 软件条款；`-a yes` 表示接受安装器的许可条款，执行它意味着你接受该条款：

```bash
mkdir -p ~/robot/bimanual-towel-folding-ryzen-zynq/ryzen-ai-npu
./install_ryzen_ai.sh -a yes \
  -p "$HOME/robot/bimanual-towel-folding-ryzen-zynq/ryzen-ai-npu/venv"
```

安装器若要求额外依赖，以实际输出和官方文档为准。检查项目期望的路径，再运行 AMD 官方 quicktest：

```bash
ls -l ~/robot/bimanual-towel-folding-ryzen-zynq/ryzen-ai-npu/venv/bin/python
source ~/robot/bimanual-towel-folding-ryzen-zynq/ryzen-ai-npu/venv/bin/activate
echo "$RYZEN_AI_INSTALLATION_PATH"
python -c "import onnxruntime as ort; print(ort.__version__); print(ort.get_available_providers())"
export LD_LIBRARY_PATH="/lib/x86_64-linux-gnu:${RYZEN_AI_INSTALLATION_PATH}/onnxruntime/lib/:${LD_LIBRARY_PATH:-}"
cd ~/robot/bimanual-towel-folding-ryzen-zynq/ryzen-ai-npu/venv/quicktest
python quicktest.py
deactivate
```

确认安装器设置的路径正确后再设置库路径。quicktest 成功编译并运行，日志出现 NPU 运算或子图，才代表独立 NPU 测试通过。仅注册 Vitis AI provider 或列出设备不足以证明实际 NPU 推理。缺少 quicktest 目录时，先核对包版本和官方结构。

### 0.8 恢复原模型与执行项目 NPU 预检

从旧机器复制完整 `act050000`、`smol030000`、processor、归一化统计、底座模型，以及 NPU ONNX、`export.json`、`fixture.npz` 和对应参考数据，放到下文第 5.5 节所列位置。传输前后对同一文件用 `sha256sum` 核对，先确认目标目录中没有需要保留的内容。旧设备编译缓存应重新验证；私钥与训练权重不要上传 GitHub。

项目固定图像预检需要 ONNX、fixture、NPU venv 和 XRT；确认齐全并阅读脚本后，在**同一个终端**运行：

```bash
cd ~/robot/bimanual-towel-folding-ryzen-zynq
sudo prlimit --pid $$ --memlock=1073741824:1073741824
bash npu_deploy/run_npu_preflight.sh
```

`prlimit` 修改当前 shell 及后续子进程的锁定内存上限；1 GiB 是上限，不是预先分配。预检默认使用用户 systemd scope，限制 16 GiB、4 核且该 scope 不使用 swap；首次编译可能较慢。成功条件是退出状态 0、日志显示 NPU 子图、三条 `IMAGE_RESULT=` 和 `NPU_CHECK_PASSED`。这只验证固定输入的视觉编码路径。

### 0.9 设备与 FPGA：先检查，再运动

```bash
cd ~/robot/bimanual-towel-folding-ryzen-zynq
lsusb
v4l2-ctl --list-devices
ip -details link show type can
ip -brief address
```

根据实际 USB 身份重新填写 `amd_hybrid_deploy_20261031/piper_towel_replica/configs/cobot_site.json` 中左右 CAN 和三路相机的占位序列号；核对 left/middle/right 画面，不沿用旧 `/dev/video*` 数字。保留原设备的供电、CAN 和终端电阻设计。

FPGA 的实际 IP 与新 PC 网卡以现场配置为准。同网段后可 `ping <FPGA实际IP>`，再依项目文档验证 FUI1 服务及 FPG1 真实 PL 回包。板端管理服务的源 IP 白名单和密钥需要与新机一致；先备份当前板端配置，**不要直接覆盖正在使用的服务或重建密钥**。

```bash
bash npu_deploy/prepare_showcase.sh --check-only
bash npu_deploy/start_showcase.sh --check-only
bash npu_deploy/run_dashboard_readonly.sh --check-only
```

接着按模拟、相机与双臂只读、FPGA 旁路、联合只读的顺序验证。确认工作区、独立物理急停、绑定与限位后才进行实机动作。`start_showcase.sh` 会实际复位和移动机械臂；当前 FPGA 是诊断旁路，不是安全联锁。

### 0.10 保存新机环境记录

```bash
cd ~/robot/bimanual-towel-folding-ryzen-zynq
mkdir -p local_environment_record
cat /etc/os-release > local_environment_record/os-release.txt
uname -a > local_environment_record/kernel.txt
lscpu > local_environment_record/cpu.txt
free -h > local_environment_record/memory.txt
lspci -nn > local_environment_record/pci.txt
amd_hybrid_deploy_20261031/.venv/bin/python -m pip freeze > local_environment_record/host-pip.txt
ryzen-ai-npu/venv/bin/python -m pip freeze > local_environment_record/npu-pip.txt
```

在初始化 XRT 的终端另存 `xrt-smi examine` 输出。分享前检查设备标识和地址，密钥不要写入记录或 Git。

本节依据：[中科大 Ubuntu 源](https://mirrors.ustc.edu.cn/help/ubuntu.html)、[中科大 PyPI 源](https://mirrors.ustc.edu.cn/help/pypi.html)、[AMD ROCm 7.14.1 安装](https://rocm.docs.amd.com/en/docs-7.14.1/install/rocm.html)、[AMD Ryzen AI 1.9 Linux 安装](https://ryzenai.docs.amd.com/en/latest/linux.html)。

## 1. 项目分工与迁移目标

原系统由三条路径组成：

1. AI PC 运行 ACT、SmolVLA、动作融合、设备调度和软件保护，通过 USB-CAN 与双 Piper 通信。
2. SmolVLA 视觉编码由独立 Ryzen AI NPU 进程运行；其余模型与控制使用主机 ROCm/PyTorch。
3. AI PC 经有线 UDP 向 Zynq 发送动作和状态的采样副本；板端 PS 调用 PL 定点检查器，结果用于日志和 LCD 显示。

本次主要更换 AI PC。模型权重、训练配置、传感器、机械臂和 FPGA 板可沿用；PC 侧系统、驱动、虚拟环境、设备绑定及网络配置需要在新机重建。**FPGA 当前是异步诊断旁路，不控制动作放行或急停。**

## 2. 旧 AI PC：现场环境基线

以下来自 2026-11-02 项目技术报告及交付目录；不代表旧机器当前状态仍完全相同。更细的软件清单以旧机实际导出的 `pip freeze` 与驱动信息为准。

| 项目 | 旧机记录 | 用途或说明 |
| --- | --- | --- |
| 整机 | ASUS TX Air FA401KM | 原 AI PC |
| CPU / 内存 | Ryzen AI 7 H 350；系统可见约 30,683 MB | 约 32 GB；主机与集成设备共享资源 |
| AMD GPU | Radeon 860M；`gfx1152` | ROCm / PyTorch 推理 |
| AMD NPU | NPU Krackan 1；`aie2p`；固件 `1.1.2.64` | 视觉编码器；BDF 是旧机地址，不应照搬 |
| 其他 GPU | NVIDIA RTX 5060 Laptop GPU 在旧机 PCI 列表中出现 | 不是本报告所述 AMD ROCm/NPU 路径的证据 |
| OS / 内核 | Ubuntu 26.04 LTS；`7.0.0-38-generic` | 原机现场记录 |
| Python | 主机 Python 3.12；板端 Python 3.8 | 主机和 NPU 使用分离环境 |
| ROCm / HIP | ROCm 7.14.1；PyTorch 报告 HIP `7.14.60850` | 原机 GPU 路径 |
| PyTorch / torchvision | `2.11.0+rocm7.14.1` / `0.26.0+rocm7.14.1` | torchvision 版本来自交付依赖清单 |
| XRT / NPU 驱动 | XRT 2.26.89；amdxdna `2.26.260102.59.release_20261022` | 原机 NPU 驱动记录 |
| ONNX / runtime | ONNX 1.22.0；opset 17；ONNX Runtime 1.30.0 | 分别为导出记录与 NPU 检查记录 |
| 板端 | 正点原子启明星 V2，Zynq-7020，`xc7z020clg400-2`；PYNQ 2.7 为历史记录 | 型号、位流、镜像版本以实机为准 |
| 相机 | 三只 Intel RealSense 435 系列，RGB 640×480，配置 30 FPS | 角色为左、中、右；当前模型不使用深度作为输入 |
| 双臂 | 2 × AgileX Piper；独立 USB-CAN；1 Mbps；旧逻辑名 `can_left` / `can_right` | 新主机重新绑定设备身份 |
| 网络 | 旧机 PC `192.168.2.10`，FPGA `192.168.2.99`；现场协商 100 Mbps | 历史示例，不是新机必须使用的地址 |
| GUI | Pillow 绘制中文，需 Noto CJK 字体；真实界面的新版本当时未完成完整验收 | 显示不作为安全保护 |

部署包的 `requirements/common.lock.txt` 还记录 `numpy==2.2.6`、`piper-sdk==0.6.2`、`python-can==4.6.1`、`transformers==5.5.4` 等。以项目依赖文件和旧机的实际环境为准，不要手工只安装这几项就认定环境完整。原技术报告：[technical_report_zh.md](https://github.com/JJ66-git/bimanual-towel-folding-ryzen-zynq/blob/main/docs/technical_report_zh.md)。

### 原模型和运行参数

| 项目 | 原记录 |
| --- | --- |
| ACT | 自训练 `act050000`；chunk size 100；`n_action_steps=50` |
| SmolVLA | 自训练 `smol030000`；chunk size 50；10 次去噪；16 层 VLM；expert width multiplier 0.75 |
| 基座 | `HuggingFaceTB/SmolVLM2-500M-Video-Instruct`；历史 revision `7b375e1b73b11138ff12fe22c8f2822d8fe03467` |
| guided RTC | guidance weight 2.5；execution horizon 10；默认第 300 步进入接管逻辑 |
| 观测 / 动作 | 三路 RGB；28 维双臂状态；14 维双臂关节与夹爪目标 |
| 视觉 NPU ONNX | 输入 `[1,3,512,512]`、输出 `[1,64,960]`；导出 float32、opset 17；设备内部精度以编译报告为准 |
| 现场动作预算 | 目标 30 Hz，任务上限 750 步；原训练 `task_frames=900` 是不同的元数据，不应随意覆盖 |

## 3. 新 HX 370 与旧机的重要区别

| 项目 | 旧机 | 新机预期 | 迁移要求 |
| --- | --- | --- | --- |
| GPU 架构 | Radeon 860M / `gfx1152` | Radeon 890M / `gfx1150` | 装适配 `gfx1150` 的 ROCm/PyTorch，实际设备名以新机识别结果为准 |
| 内存 | 约 32 GB | 32 GB | 主机、集显和 NPU 相关工作共享系统资源；先离线分阶段测试峰值 |
| NPU | Krackan 1 / `aie2p` | HX 370 为 Strix Point 系列，实际驱动与设备信息待测 | NPU 驱动及编译缓存重新验证，不直接复制旧机器缓存作为已适配 |
| 外设 | 已按旧机身份绑定 | 沿用相机、CAN 和 Zynq | 核对新网口、IP、相机顺序和 USB-CAN 身份 |

AMD 的 [GPU 规格表](https://rocm.docs.amd.com/en/latest/reference/gpu-specs.html)列出 HX 370 的 Radeon 890M 为 `gfx1150`。**仓库现有 `amd_hybrid_deploy_20261031/requirements/rocm-gfx1152.txt` 及 `tools/install_env.sh`、`download_wheels.sh`、`continue_host_install.sh` 硬编码旧架构，不能当成 HX 370 已适配的一键安装器。**不要靠 `HSA_OVERRIDE_GFX_VERSION` 强行把新机冒充旧机。

AMD Ryzen AI Software 1.9 的 [Linux 安装文档](https://ryzenai.docs.amd.com/en/latest/linux.html)列出 Ubuntu 24.04 LTS、Python 3.12 和推荐 64 GB 内存。32 GB 是低于推荐值而不是已证明不能运行：原系统在约 32 GB 上有运行记录，但新 HX 370 的完整混合任务、峰值内存和持续稳定性仍待实际验证。原 NPU 预检脚本默认给用户 scope 16 GiB、4 核、禁用该 scope 的 swap；设备共享内存未必全部计入。不要据此认为整机一定足够。

## 4. 新机推荐系统方案（待新机实测）

**首选候选：Ubuntu 24.04.4 LTS 桌面版，ROCm 7.14.1，Python 3.12。** Ryzen AI NPU 按 AMD Linux 官方文档安装，使用与新内核匹配的 XRT / amdxdna。这个组合是迁移起点，不是已验证的新机配置。

- AMD [ROCm 7.14.1 兼容性矩阵](https://rocm.docs.amd.com/en/docs-7.14.1/compatibility/compatibility-matrix.html)的 Ryzen 部分列出 Ubuntu 24.04.4 **OEM 6.17** 与 Ubuntu 26.04 **GA 7.0**。镜像初装的内核不一定就是所列 OEM 内核；安装后用 `uname -r` 核对，再按照 AMD 与整机厂商说明处理内核和驱动。
- Ubuntu 24.04 是当前 Ryzen AI 1.9 Linux 指南列出的发行版。原机 Ubuntu 26.04 是现场运行记录；不能由此推断新机在 26.04 上的 NPU 软件组合获得官方支持。
- ROCm 7.14.1 与原机一致，也满足项目保存的赛道要求中 ROCm 7.14.0 及以上的版本门槛；但兼容性仍应逐项核对 GPU、OS、内核、驱动和软件包。
- 不要混用面向 Radeon 独显的驱动指令与 Ryzen APU 指令。AMD 的 [7.14.1 安装页](https://rocm.docs.amd.com/en/docs-7.14.1/install/rocm.html)说明受支持 Ryzen APU 使用对应 Ubuntu 内置内核驱动；安装前以具体设备和该版本矩阵为准。

## 5. 新机安装顺序

### 5.1 核对已安装的系统

本机 Ubuntu 24.04 已安装；无需重复制作启动盘或重装系统。详细操作以第 0 节为准。以下是需要记录的基础信息：

```bash
cat /etc/os-release
uname -r
lscpu | grep -E 'Model name|CPU\(s\)'
free -h
df -h /
```

按兼容矩阵核对内核与机型；驱动版本确定前不要随意混装来自不同 ROCm 版本的驱动。确认核显、网络、存储和 USB 可用后再继续。

### 5.2 获取项目与准备主环境

```bash
sudo apt update
sudo apt install git python3.12 python3.12-venv pciutils usbutils
git clone https://github.com/JJ66-git/bimanual-towel-folding-ryzen-zynq.git
cd bimanual-towel-folding-ryzen-zynq
```

主环境目标路径为 `amd_hybrid_deploy_20261031/.venv`；NPU 环境目标路径为项目根目录的 `ryzen-ai-npu/venv`。两个环境分开维护。创建虚拟环境前先核对实际 Python 3.12 是否可用：

```bash
python3.12 --version
python3.12 -m venv amd_hybrid_deploy_20261031/.venv
```

旧包中的 `requirements/rocm.constraints.txt` 固定原版 ROCm / PyTorch / torchvision，`requirements/common.lock.txt` 是主机其余依赖。**新机应先建立并验证 `gfx1150` 软件包方案，再按锁文件安装并做 `pip check`；不能直接执行旧版 `tools/install_env.sh rocm`。**

AMD 的 7.14.1 安装文档列出了针对 `gfx1150` 的 Python 包形式：

```bash
amd_hybrid_deploy_20261031/.venv/bin/python -m pip install \
  --index-url https://repo.amd.com/rocm/whl-multi-arch/ \
  'rocm[libraries,device-gfx1150]==7.14.1'
```

这是 AMD 的 GPU 架构包安装示例，不包含完整 PyTorch、项目依赖或内核驱动。新机的 PyTorch 与 torchvision 应优先核对 AMD 对 **ROCm 7.14.1、Python 3.12、gfx1150** 的 wheel，再与原项目 `2.11.0+rocm7.14.1` / `0.26.0+rocm7.14.1` 约束一致性检查；若该组合依赖不可满足，记录实际错误，不擅自升降模型栈版本。必要时先在隔离环境试装并保存 `pip freeze`。安装源和路径可能随 AMD 维护变化，以 [ROCm 7.14.1 官方安装页](https://rocm.docs.amd.com/en/docs-7.14.1/install/rocm.html)为准。

### 5.3 验证 AMD GPU

系统与驱动安装后，记录 `lspci` 与 DRM 设备：

```bash
lspci -nn | grep -Ei 'vga|display|3d|amd|nvidia'
ls -l /dev/kfd /dev/dri/renderD* 2>/dev/null
```

在主虚拟环境中运行：

```bash
amd_hybrid_deploy_20261031/.venv/bin/python - <<'PY'
import torch
print('torch:', torch.__version__)
print('HIP:', torch.version.hip)
print('GPU available:', torch.cuda.is_available())
if torch.cuda.is_available():
    print('GPU:', torch.cuda.get_device_name(0))
    x = torch.randn((512, 512), device='cuda')
    y = x @ x
    torch.cuda.synchronize()
    print('matrix result:', y.shape, float(y[0, 0]))
PY
```

PyTorch 的 ROCm 接口也使用 `torch.cuda`；应结合 HIP、GPU 名称和实测计算确认 AMD 路径。若另有独显，确认实际选择的设备。记录是否识别到 `gfx1150`，但不要将单次矩阵运算称为整机项目验收。

### 5.4 安装 NPU 驱动与独立环境

按 [AMD Ryzen AI 1.9 Linux 指南](https://ryzenai.docs.amd.com/en/latest/linux.html)核对 24.04、内核、XRT、amdxdna 与 Ryzen AI 软件包的版本，再安装；旧机 XRT 2.26.89 仅是基线。避免把整台旧机的 `.venv` 原封复制到不同的系统和 GPU 架构。

```bash
source /opt/xilinx/xrt/setup.sh
xrt-smi examine
```

识别到 NPU 只说明驱动发现设备。项目 `npu_deploy/vision_client.py` 固定查找项目根目录 `ryzen-ai-npu/venv/bin/python` 与 `lib/python3.12/site-packages`，还引用 `/opt/xilinx/xrt` 和旧的 Python 库路径；安装后需逐项核对，不要直接套用旧路径。主环境与 NPU worker 环境应分别保存 `pip freeze`、驱动版本和软件包来源。

### 5.5 从原机迁移训练成果

原机完整保留并传输：

- `act050000` 与 `smol030000` 完整 checkpoint 目录、各自配置、processor、归一化统计和 tokenizer；确认模型编号与实际来源一致。
- 对应 SmolVLM2 基座、模型处理器与必要缓存；确认 revision、训练输入维度、RGB 通道顺序一致。
- 原项目控制配置、起点、关节限位、夹爪与 RTC 配置；按原机核对，不能为通过测试直接放宽阈值。
- NPU 视觉 ONNX、`export.json`、`fixture.npz` 和固定输入/参考输出。新设备编译缓存必须重新验证；ONNX 能否沿用取决于模型与运行时一致性。
- 板端 `.bit`、`.hwh`、PYNQ 镜像身份、网络及服务配置。沿用原板时先备份现有 SD 卡/配置，再考虑任何重新部署。

预期模型位置：

```text
bimanual-towel-folding-ryzen-zynq/
├── amd_hybrid_deploy_20261031/piper_towel_replica/models/
│   ├── act050000/
│   └── smol030000/
├── npu_deploy/artifacts/smol_vision/
│   ├── smol_vision.onnx
│   ├── export.json
│   └── fixture.npz
└── ryzen-ai-npu/venv/
```

原技术报告记录了权重和 ONNX 的 SHA-256；先确认其中摘要对应的具体文件，再对迁移前后同一文件运行 `sha256sum 文件路径` 核对。旧配置中的设备序列号属于本机信息，公开仓库已替换为占位符，迁移后应从当前设备重新绑定；密钥不进入公开仓库。

## 6. 新机分阶段验收

1. **系统与设备**：记录系统/内核，验证 `gfx1150`、ROCm、PyTorch 实际 GPU 运算与 `xrt-smi examine`。记录内存占用、温度和版本。
2. **模型离线**：加载自有 ACT / SmolVLA checkpoint；检查 3 图像、28 维状态、14 维动作和预处理约定；同输入对照旧机输出，记录误差与精度。
3. **NPU 单独检查**：核对 `smol_vision.onnx`、`fixture.npz`、NPU venv、XRT；查看 `npu_deploy/run_npu_preflight.sh` 的前置条件和资源限制，先完成独立固定图像检查。日志需有 NPU 子图和有效输出；`NPU_PARTITION_CONFIRMED` 只说明分区，`NPU_FIRST_INFERENCE_COMPLETED` 才说明至少一次视觉推理完成。
4. **三路相机 / 双臂只读**：重新绑定左中右相机和左右 USB-CAN，核对角色与反馈，先用只读界面，避免同时被其他进程占用。
5. **FPGA 独立检查**：核对网络和原板服务。`ping` 只代表网络可达；FUI1 服务响应不等于 PL 检查完成，需要 FPG1 回包与整数参考对照。
6. **联合只读**：先检查项目入口的 `--check-only`，然后再按指南进行真实相机与板端旁路只读联调。旧版文档可能与 5 秒空闲会话修正后的板端副本不一致，现场以实际板端版本为准。
7. **真实动作**：确认工作区、独立急停、连接和限位；逐级复位和小范围测试，再进行完整任务。`start_showcase.sh` 会实际复位和驱动双臂；Ctrl+C 和 FPGA 屏幕不是急停。

每个阶段记录测试日期、模型文件、版本、输入、运行结果和峰值内存。不要把一次 NPU 输出或 `ping` 当成完整系统通过。

### 项目现有入口（从项目根目录执行）

```bash
bash npu_deploy/prepare_showcase.sh --check-only
bash npu_deploy/start_showcase.sh --check-only
bash npu_deploy/run_dashboard_readonly.sh --check-only
```

这些入口要求相应路径、模型和环境已经准备；检查失败时先读报错，不继续真实动作。完整流程见 [技术报告第 11 节](https://github.com/JJ66-git/bimanual-towel-folding-ryzen-zynq/blob/main/docs/technical_report_zh.md)。

## 7. 新机实际安装记录（2026-11-13）

项目实际位置：`/home/ysyx/FPGA_competition/bimanual-towel-folding-ryzen-zynq`。
命令中的示例 `~/robot/...` 应替换为此目录。原项目代码未修改。

| 项目 | 新机结果 |
| --- | --- |
| 整机 / BIOS / 磁盘 | Mini GTR；BIOS `STXP3_F8_10_IEC0107_BI0006_Machenike_54W`，2025-10-20；根分区约 915 GiB |
| CPU / 内存 | Ryzen AI 9 HX 370，24 逻辑 CPU；系统可见约 27 GiB，swap 8 GiB |
| 独立显卡 | 本次 PCI 枚举未发现 NVIDIA 或其他独立显卡 |
| Ubuntu / 内核 | Ubuntu 24.04.5 LTS；`7.0.0-31-generic`；未替换内核，与 AMD 矩阵列出的 Ubuntu 24.04.4 / OEM 6.17 组合不同 |
| GPU / 架构 / 驱动 | AMD Radeon 890M Graphics；`gfx1150`；系统内置 amdgpu |
| ROCm / PyTorch / torchvision / HIP | `7.14.1` / `2.11.0+rocm7.14.1` / `0.26.0+rocm7.14.1` / `7.14.60850`，AMD 官方索引 |
| 主环境 | `amd_hybrid_deploy_20261031/.venv`；Python 3.12；锁文件全部版本核对一致；`pip check` 无冲突 |
| 软件源 / 基础工具 | 普通 Ubuntu、用户 pip 与主环境 pip 使用中科大；安全源使用官方 security.ubuntu.com；基础依赖已安装。NPU 安装因镜像返回 403 改用官方 PyPI，并在 NPU 环境内单独保留此源 |
| 用户权限 | ysyx 已加入 render、video；本会话用 `sg render` 验证设备访问。普通新终端须注销重登录以刷新组 |
| GPU 验收 | 512×512 GPU 矩阵运算有限值检查通过；项目 doctor `basic_backend_check_passed: true` |
| XRT / amdxdna | XRT `2.26.89` / amdxdna `2.26.260102.59.release_20261022` 已安装；DKMS installed；`xrt-smi examine` 识别 `NPU Strix / aie2p`，固件 `1.1.2.64`；XRT GEMM、latency、throughput 硬件测试全部 PASSED（默认功耗模式：约 51 TOPS、52 us、94215 op/s；非模型性能）；安装后 GPU 复验通过 |
| Ryzen AI runtime / NPU 环境 | Ryzen AI `1.9.0` 已安装到 `ryzen-ai-npu/venv`；ONNX Runtime VitisAI `1.30.0`、ONNX `1.22.0`、NumPy `2.5.3`、PyTorch `2.10.0+cpu`；独立环境 `pip check` 无冲突；官方 quicktest 退出 0，日志同时有 `Actually running on NPU 1` 与 `Test Finished` |
| 展示入口 | prepare_showcase、start_showcase、run_dashboard_readonly 的 `--check-only` 均退出 0；这只是入口预览，不代表硬件或模型齐备 |
| ACT / SmolVLA / ONNX / fixture | 本机缺失，须从旧机迁移并核对哈希 |
| 相机 / USB-CAN / FPGA | 本次未发现项目相机和 CAN；有线网口 enp195s0 为 DOWN；未进行板端配置或真实动作 |
| 项目模型 / 外设 / 联合验收 | 环境与官方 NPU quicktest 已通过；项目 ONNX/fixture、ACT/SmolVLA、相机、双臂、FPGA 联合测试仍待资产和设备齐备后进行 |

工作区 `local_environment_record/` 保存系统、项目 commit、pip freeze、GPU 测试、doctor、入口预览、安装日志和 XRT 包 SHA-256。

基础工具脚本：`setup_system_hx370.sh`（已执行）；GPU 复验：`sg render -c 'bash /home/ysyx/FPGA_competition/verify_hx370.sh'`。
XRT 系统安装脚本：`setup_npu_xrt_hx370.sh`（用户已执行，安装成功）。
运行时完整下载与解压已完成；安装包 SHA-256：`local_environment_record/ryzen-runtime-sha256.txt`。下载日志及断点状态仍保留，便于追溯。

NPU 复验：`sg render -c 'bash /home/ysyx/FPGA_competition/verify_npu_hx370.sh'`。本次实际验收在 systemd 用户 scope 内限制 16 GiB、4 核、禁用该 scope 的 swap。

兼容修正：AMD runtime 自带 XRT coreutil 与系统版本的 SHA-256 完全相同，但缺少本地查找路径。在 `ryzen-ai-npu/venv/lib/python3.12/site-packages/voe/lib/` 内补充以下符号链接：

```text
libxrt_core.so.2 -> /opt/xilinx/xrt/lib/libxrt_core.so.2
libxrt_driver_xdna.so.2 -> /opt/xilinx/xrt/lib/libxrt_driver_xdna.so.2
```

安装器提示 `/usr/include/asm` 不存在；系统实际提供 `/usr/include/x86_64-linux-gnu/asm`。本次未修改系统头文件目录，官方 quicktest 已成功编译并运行。若之后额外工具出现头文件查找错误，应针对该工具检查 include 路径，不能把本次 quicktest 通过扩展为所有编译工具均已验收。

最终测试日志：`local_environment_record/npu-quicktest.log`；独立环境版本：`npu-versions.json`；两套环境的 pip freeze 分别为 `host-pip.txt`、`npu-pip.txt`。失败的中科大镜像引导环境已移到工作区 `downloads/ryzen-npu-bootstrap-mirror-attempt/` 保留，避免进入项目版本控制。

## 8. 参考资料

- [项目中文技术报告：原机环境、模型与复现步骤](https://github.com/JJ66-git/bimanual-towel-folding-ryzen-zynq/blob/main/docs/technical_report_zh.md)
- [项目展示与现场操作指南](https://github.com/JJ66-git/bimanual-towel-folding-ryzen-zynq/blob/main/npu_deploy/showcase_guide_zh.md)
- [AMD ROCm 7.14.1 兼容性矩阵](https://rocm.docs.amd.com/en/docs-7.14.1/compatibility/compatibility-matrix.html)
- [AMD ROCm 7.14.1 安装指南](https://rocm.docs.amd.com/en/docs-7.14.1/install/rocm.html)
- [AMD Ryzen AI Linux 安装指南](https://ryzenai.docs.amd.com/en/latest/linux.html)
- [AMD GPU 规格与 gfx 架构](https://rocm.docs.amd.com/en/latest/reference/gpu-specs.html)
- [Ubuntu Desktop 安装教程](https://ubuntu.com/tutorials/install-ubuntu-desktop)
