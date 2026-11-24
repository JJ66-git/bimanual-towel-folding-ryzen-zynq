"""Explicit operator outcome; a matched FUI acknowledgement is not a PL verdict."""
import datetime
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def confirm(address,session,success):
    if address!=['192.168.2.99',9009] and address!=('192.168.2.99',9009):
        raise ValueError('Unexpected local FPGA endpoint')
    if type(success) is not bool:raise ValueError('An explicit operator choice is required')
    sys.path.insert(0,str(ROOT/'fpga_integration/fpga_code/fpga_workspace'))
    from host.task_status import Client
    client=Client(tuple(address),session=session)
    try:ack=client.send('COMPLETE' if success else 'FAILED','IDLE','OPERATOR')
    finally:client.close()
    record=dict(confirmed_at=datetime.datetime.now().astimezone().isoformat(),
                task_session=session,outcome='success' if success else 'failure',
                basis='operator_confirmation',ack=ack,robot_commands_sent=False)
    folder=ROOT/'npu_deploy/artifacts/task_outcomes';folder.mkdir(parents=True,exist_ok=True)
    path=folder/('outcome_'+str(time.time_ns())+'.json')
    path.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    return record
