#!/usr/bin/env python3
"""Verify and link the separately distributed FlexiFold runtime models."""

import argparse
import hashlib
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = Path("amd_hybrid_deploy_20261031")
POLICY = BUNDLE / "piper_towel_replica"
ASSET_PATHS = (
    (Path("checkpoints/act050000"), POLICY / "models/act050000"),
    (Path("checkpoints/smol030000"), POLICY / "models/smol030000"),
    (Path("vision/smolvlm2_500m"), BUNDLE / "assets/smolvlm2_500m"),
    (Path("vision/smol_vision.onnx"), Path("npu_deploy/artifacts/smol_vision/smol_vision.onnx")),
    (Path("torch"), POLICY / "deployment_assets/torch"),
)


def verify(path, expected):
    if not path.is_file():
        raise ValueError(f"缺少文件：{path}")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != expected:
        raise ValueError(f"SHA-256 不匹配：{path}")


def bind(source, *, check_only):
    manifest = {}
    for line in (ROOT / "docs/runtime_assets.sha256").read_text().splitlines():
        digest, name = line.split("  ", 1)
        manifest[Path(name)] = digest

    links = []
    for source_name, target_name in ASSET_PATHS:
        source_item = source / source_name
        target = ROOT / target_name
        required = [item for item in manifest if item.is_relative_to(target_name)]
        if not required:
            raise ValueError(f"校验清单缺少：{target_name}")
        for item in required:
            verify(source_item / item.relative_to(target_name), manifest[item])
        if target.is_symlink() and target.resolve() == source_item.resolve():
            print(f"已有正确链接 {target_name}")
        elif target.exists() or target.is_symlink():
            for item in required:
                verify(ROOT / item, manifest[item])
            print(f"保留已验证的现有资产 {target_name}")
        else:
            links.append((source_item, target))

    if check_only:
        print(f"资产校验通过，待建立 {len(links)} 个链接。")
        return

    created = []
    try:
        for original, target in links:
            target.parent.mkdir(parents=True, exist_ok=True)
            relative = os.path.relpath(original, target.parent)
            target.symlink_to(relative, target_is_directory=original.is_dir())
            created.append((target, relative))
    except OSError:
        for target, relative in reversed(created):
            if target.is_symlink() and os.readlink(target) == relative:
                target.unlink()
        raise
    print(f"资产校验通过，已建立 {len(created)} 个链接。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT.parent / "FlexiFold_Runtime_Assets",
                        help="本地权重文件夹，默认使用仓库旁的 FlexiFold_Runtime_Assets")
    parser.add_argument("--check-only", action="store_true", help="校验原件和已有链接")
    args = parser.parse_args()
    try:
        bind(args.source.expanduser().resolve(strict=True), check_only=args.check_only)
    except (OSError, ValueError) as error:
        print(f"配置运行资产失败 · {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
