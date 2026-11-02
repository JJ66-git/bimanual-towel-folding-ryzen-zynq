# Public release checks / 公开版核验

## Release scope

- Hardware serials in `cobot_site.json` and associated test fixtures were replaced with placeholders; users must bind their own devices. The engineering configuration (including the 14-dimensional start target and limits) and FPGA source/build outputs remain in the package.
- Trained checkpoints, raw training images, videos, field logs, credentials and local environments are not included. Existing aggregate results referencing unavailable logs cannot be independently recomputed from this clone.
- Project-authored source is offered under Apache-2.0. Vendored third-party materials retain their licenses, notices and attribution. Hardware, models and external dependencies may carry additional conditions.
- The board/LCD photograph is a real photograph of a diagnostic screen; the GUI screenshot is simulated.

## Checks performed on the public copy (2026-11-02)

- 685 Python files were parsed successfully for syntax on the preparation PC.
- Ten offline tests passed in three groups: FPGA shadow (5), field helpers (3), and dashboard read-only checks (2). These tests use fakes and do not validate physical devices.
- The full host suite was not validated on this preparation PC. Windows lacks the Linux-specific Noto CJK font and symlink setup; the available WSL environment lacks project dependencies and AMD XRT. The historical source-machine results are described separately in [verification_zh.md](verification_zh.md) and do not prove a fresh installation.
- The competition still requires original supporting evidence, independently defined physical task success, physical end-to-end latency, AI task quality, and other measurements; see [metrics](../metrics/metrics.csv) and [requirements](track_requirements.md).

## 公开说明

公开包将机械臂和相机序列号替换为占位符，保留完成工程所需的起点、限位配置和 FPGA 设计；不包含训练权重、原始影像、运行日志、密钥或本机环境。10 项离线测试通过，完整系统尚未在干净环境和真实硬件重新验证，比赛要求的完整证据仍需另行补充。
