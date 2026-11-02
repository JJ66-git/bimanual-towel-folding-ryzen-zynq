#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
FILE="$ROOT/npu_deploy/vendor/flm_vla.tar.gz"
mkdir -p "$ROOT/npu_deploy/vendor"
echo '尝试 HF-Mirror；若支持断点续传，将继续使用已有的部分文件。'
wget --continue -O "$FILE" \
  'https://hf-mirror.com/FastFlowLM/smolvla-NPU2/resolve/main/flm_vla.tar.gz'
echo '下载完成，正在从 Hugging Face 官方元数据核对 SHA-256……'
HF_HUB_OFFLINE=0 "$ROOT/amd_hybrid_deploy_20261031/.venv/bin/python" - "$FILE" <<'PY'
import hashlib
import sys
from pathlib import Path
from huggingface_hub import HfApi

path = Path(sys.argv[1])
info = HfApi(endpoint='https://huggingface.co', token=False).model_info(
    'FastFlowLM/smolvla-NPU2', files_metadata=True, token=False)
entry = next(item for item in info.siblings if item.rfilename == 'flm_vla.tar.gz')
if entry.lfs is None or not entry.lfs.sha256:
    raise SystemExit('官方元数据中没有找到 SHA-256；未完成校验。')
h = hashlib.sha256()
with path.open('rb') as stream:
    for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
        h.update(chunk)
print('本地 SHA-256:', h.hexdigest())
print('官方 SHA-256:', entry.lfs.sha256)
if h.hexdigest() != entry.lfs.sha256:
    raise SystemExit('校验失败：文件与官方当前版本不一致，请勿使用。')
print('官方 SHA-256 校验通过。')
PY
