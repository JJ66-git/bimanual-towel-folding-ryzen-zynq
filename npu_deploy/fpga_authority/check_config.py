#!/usr/bin/env python3
"""Lightweight authority preflight, before any reset/model/arm connection."""
import argparse
import json
import os
from pathlib import Path
import sys

PROJECT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PROJECT/'amd_hybrid_deploy_20261031/piper_towel_replica/deployment_entry'))
from fpga_action_authority import load_execution_config,verify_service


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default=os.environ.get('AIPC_FPGA_AUTHORITY_CONFIG'))
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--verify-service',action='store_true')
    args=parser.parse_args()
    if not args.config:parser.error('Provide an explicit authority config')
    config,_=load_execution_config(args.config,execute=args.execute)
    if args.verify_service:
        if not args.execute:parser.error('--verify-service requires --execute')
        verify_service(config)
    print('FPGA_AUTHORITY_CONFIG='+json.dumps(dict(valid=True,execution_qualified=args.execute and '_commissioning' not in config,
        commissioning='_commissioning' in config,board_service_checked=args.verify_service,robot_commands_sent=False)))
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print('FPGA 核准模式尚未就绪，未进入复位或任务动作。',file=sys.stderr)
        print(type(error).__name__+': '+str(error),file=sys.stderr)
        raise SystemExit(1)
