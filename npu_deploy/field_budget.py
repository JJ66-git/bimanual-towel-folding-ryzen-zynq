#!/usr/bin/env python3
"""Align the local live action budget with the current FPGA profile."""
import argparse
import json
import os
from pathlib import Path
import stat
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'amd_hybrid_deploy_20261031/piper_towel_replica/configs/cobot_site.json'
PROFILES = (ROOT / 'fpga_integration/fpga_code/fpga_workspace/config/guard_profile.json',
            ROOT / 'fpga/config/guard_profile.json')


def check(apply=False):
    profile_path = next((p for p in PROFILES if p.is_file()), None)
    if profile_path is None:
        raise RuntimeError('未找到 FPGA 配置；无法核对动作预算。')
    profile = json.loads(profile_path.read_text())
    before = SITE.read_bytes()
    site = json.loads(before)
    if profile.get('max_actions') != 750 or profile.get('handoff_step') != 300:
        raise RuntimeError('FPGA 源码配置不是预期的 750 步、300 步交接，请核对板端版本。')
    if site.get('handoff_step') != 300:
        raise RuntimeError('现场交接步数不是 300，未修改。')
    changed = False
    if site.get('max_actions') != 750:
        if not apply:
            raise RuntimeError('现场动作预算不是 750，请先运行准备脚本。')
        site['max_actions'] = 750
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=SITE.parent,
                                             prefix='.site_budget_', suffix='.tmp', delete=False) as f:
                temporary = Path(f.name)
                f.write(json.dumps(site, indent=2, ensure_ascii=False)+'\n')
            os.chmod(temporary, stat.S_IMODE(SITE.stat().st_mode))
            if SITE.read_bytes() != before:
                raise RuntimeError('现场配置同时被修改，未覆盖。')
            temporary.replace(SITE)
            changed = True
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    print('FIELD_BUDGET='+json.dumps(dict(site_steps=site['max_actions'], fpga_profile_steps=profile['max_actions'],
        handoff_step=300, site_changed=changed,
        note='板上 overlay 身份需结合 FPGA 实际回复核对；原始训练数据帧数不修改。'), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    check(parser.parse_args().apply)
