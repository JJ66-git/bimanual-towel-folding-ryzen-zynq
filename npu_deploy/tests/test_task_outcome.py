"""Operator outcome receipts use mocked UDP clients and temporary output folders."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'npu_deploy'),str(ROOT/'fpga_integration/fpga_code/fpga_workspace')]
import task_outcome


class TaskOutcomeTests(unittest.TestCase):
    def test_choices_use_current_session_and_explicit_operator_provenance(self):
        for success,phase,outcome in ((True,'COMPLETE','success'),(False,'FAILED','failure')):
            with tempfile.TemporaryDirectory() as temporary,patch.object(task_outcome,'ROOT',Path(temporary)),patch('host.task_status.Client') as client:
                client.return_value.send.return_value=dict(session=123,phase=phase,origin='OPERATOR')
                record=task_outcome.confirm(['192.168.2.99',9009],123,success)
                client.assert_called_once_with(('192.168.2.99',9009),session=123)
                client.return_value.send.assert_called_once_with(phase,'IDLE','OPERATOR')
                self.assertEqual(record['basis'],'operator_confirmation');self.assertEqual(record['outcome'],outcome)
                self.assertFalse(record['robot_commands_sent'])
                saved=list(Path(temporary).rglob('outcome_*.json'))
                self.assertEqual(len(saved),1);self.assertEqual(json.loads(saved[0].read_text()),record)
    def test_no_acknowledgement_does_not_create_a_success_receipt(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(task_outcome,'ROOT',Path(temporary)),patch('host.task_status.Client') as client:
            client.return_value.send.side_effect=TimeoutError('no matched acknowledgement')
            with self.assertRaises(TimeoutError):task_outcome.confirm(['192.168.2.99',9009],123,True)
            client.return_value.close.assert_called_once();self.assertEqual(list(Path(temporary).rglob('*.json')),[])
    def test_endpoint_and_explicit_choice_are_required(self):
        with patch('host.task_status.Client') as client:
            for address,choice in ((['127.0.0.1',9009],True),(['192.168.2.99',9009],'success')):
                with self.assertRaises(ValueError):task_outcome.confirm(address,123,choice)
            client.assert_not_called()


if __name__=='__main__':unittest.main()
