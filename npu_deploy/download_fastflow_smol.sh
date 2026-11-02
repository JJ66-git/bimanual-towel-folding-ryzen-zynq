#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/npu_deploy/vendor"
wget --continue -O "$ROOT/npu_deploy/vendor/flm_vla.tar.gz" \
  'https://huggingface.co/FastFlowLM/smolvla-NPU2/resolve/main/flm_vla.tar.gz'
sha256sum "$ROOT/npu_deploy/vendor/flm_vla.tar.gz"
echo '下载完成。'
