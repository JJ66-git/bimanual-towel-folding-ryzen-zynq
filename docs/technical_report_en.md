# Technical overview: Ryzen AI and Zynq bimanual manipulation

This is an English technical overview of the project snapshot, not a line-by-line translation. The detailed Chinese report is [technical_report_zh.md](technical_report_zh.md); the competition text is [track_requirements.md](track_requirements.md). Where historical documents disagree, check dates, source code and evidence before drawing conclusions.

## Objective and system

The prototype explores a dual-arm towel-folding task with three RGB streams and two AgileX Piper arms. An AMD Ryzen AI PC acquires observations and arm feedback, runs policy inference and bounded action mixing, and sends arm and gripper targets through USB-CAN. The PC and a Zynq-7020 communicate over wired Ethernet/UDP for asynchronous diagnostics. The FPGA does not decide whether a robot action is allowed; neither the diagnostic results nor the independent watchdog currently form an emergency stop.

## Hardware and recorded software

The recorded PC is an ASUS TX Air FA401KM with Ryzen AI 7 H 350 and Radeon 860M (`gfx1152`), approximately 30 GB system memory and a Ryzen AI NPU. Three Intel RealSense 435-series cameras provide RGB at a configured 640×480, 30 FPS. Two Piper follower arms use separate USB-CAN adapters and power supplies. The board is an Alientek Zynq-7020, part `xc7z020clg400-2`, running PYNQ with an 800×480 LCD. The camera feeds are displayed on the PC; the FPGA LCD is a diagnostic status display, not a three-stream video accelerator.

The field record lists Ubuntu 26.04, ROCm 7.14.1, PyTorch 2.11.0+rocm7.14.1, XRT 2.26.89, and host Python 3.12. These are recorded versions, not a portability or official-support guarantee. Check the relevant AMD compatibility documentation before installing.

## Policy and data path

The model state contains 28 values: left and right joint/gripper positions and efforts. The 14-dimensional action contains six joint targets and one gripper target per arm. ACT provides a 100-step chunk with 50 action steps as configured in the checkpoint. SmolVLA uses a 50-step chunk and ten denoising steps; guided RTC asynchronously generates a limited correction applied to arm targets, while gripper authority stays with ACT. Existing normalization, input channel conventions and model feature ordering must be preserved.

The SmolVLA vision encoder and connector are exported with an ONNX input shape `[1,3,512,512]` and output `[1,64,960]`; the worker executes that subgraph via the Ryzen AI NPU path. The rest of the policy, guided computation and robot control remain on the host ROCm/PyTorch path. A provider registration alone is not proof of completed NPU inference; consult the field logs. The trained models and original data are not included.

## FPGA implementation and protocol

The Zynq PS receives the UDP packets, validates message/session fields and CRC, calls the PL through AXI4-Lite/MMIO, and logs or displays results. The PL implements a fixed-point `motion_guard` plus watchdog and LCD logic. Each diagnostic sample contains six signed 14-element arrays: target, measured, previous, ACT, correction and previous correction, with step, flags and profile metadata. Values use a scale of 1,000,000, giving microradians for angles and micrometers for gripper distances. Checks cover configured ranges, step changes, correction magnitude and rate, gripper ownership and optional fields. See the [protocol and register mapping](../fpga_integration/fpga_code/fpga_workspace/docs/protocol.md).

This is a diagnostic mirror of the action path. A displayed nominal phase is a configured label, not a verified grasp or fold. Feedback-age checking was disabled in the reported profile. The watchdog is not fed by the diagnostic task stream as a physical safety interlock, and a lost packet does not cause a robot stop.

## Measurements and limitations

The supplied [metrics table](../metrics/metrics.csv) records a 750-step `execute=True` run and 750/750 integer-reference matches; 751 requests including a probe had no recorded timeouts. For 750 task samples, UDP round-trip P50/P95/P99/max were 4.644/5.313/7.175/25.537 ms, while board-side PS/MMIO/PL processing was 2.128/2.286/2.512/5.011 ms. These are summaries from original logs not included in this public package, so they cannot be independently recomputed from the clone. They do not represent pure RTL delay or sensor-to-physical-action latency.

One LCD+guard implementation report records 6,357 LUTs, 96 BRAM tiles and 4.616 ns WNS under its stated constraints. Build reports are included in the FPGA workspace. Neither resource use nor timing closure establishes task success or robot safety. The formal task success rate, full physical end-to-end latency, AI task-quality assessment, clean-environment reproduction, and competition evidence remain to be completed.

## Reproduction and safety

Start with the [README](../readme.md), the [Chinese setup sections](technical_report_zh.md) and the [showcase guide](../npu_deploy/showcase_guide_zh.md). Restore the trained checkpoints and model dependencies separately, adapt device names and networks, and validate software with synthetic inputs before connecting hardware. The actual reset and control scripts can move the arms. Provide a clear workspace and an independent reachable physical emergency stop. The present FPGA design does not provide that stop. Do not substitute tick intervals or UDP RTT for a measured physical response latency.

## Sources and attribution

The code snapshot vendors modified LeRobot and ACT-related materials; consult the included [third-party notices](../amd_hybrid_deploy_20261031/third_party_notices.md) and the licenses in their directories. Key upstream sources include [LeRobot](https://github.com/huggingface/lerobot), [ACT](https://arxiv.org/abs/2304.13705), [SmolVLA](https://huggingface.co/docs/lerobot/smolvla), [Ryzen AI Software](https://ryzenai.docs.amd.com/en/latest/), [ROCm on Radeon and Ryzen](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/), and [PYNQ](https://pynq.readthedocs.io/en/latest/).
