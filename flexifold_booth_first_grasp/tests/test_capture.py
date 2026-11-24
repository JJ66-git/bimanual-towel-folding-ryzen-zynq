"""Manual start/stop, persistence and raw V/L/A provenance, without hardware."""
import base64
from io import BytesIO
import json
from pathlib import Path
import tempfile
import time
import unittest

from capture import Collector


class FakeLeader:
    fresh = True
    def snapshot(self):
        if not self.fresh: return None
        return dict(joint_deg=[1,2,3,4,5,6], gripper_mm=12, component_age_ms={'0x155':1})


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.leader=FakeLeader()
        self.collector=Collector(self.leader, self.temp.name)

    def tearDown(self):
        self.collector.close(); self.temp.cleanup()

    def test_manual_stop_after_more_than_three_seconds_and_repeated_episodes(self):
        start=self.collector.start('抓住毛巾一角')
        path=Path(start['saved_dir'])
        self.assertTrue((path/'samples.jsonl').exists())
        time.sleep(3.15)
        state=self.collector.status()
        self.assertEqual(state['state'], 'recording')
        self.assertGreater(state['duration_ms'], 3000)
        self.assertGreater(state['sample_count'], 65)
        with self.assertRaises(RuntimeError): self.collector.start('不应该并行开始')
        value=self.collector.stop(start['episode_id'])
        self.assertEqual(value['state'], 'complete')
        metadata=json.loads((path/'metadata.json').read_text())
        self.assertEqual(path.parent.name, 'episodes')
        self.assertEqual(metadata['source'], 'piper_leader_commands')
        self.assertEqual(value['samples'][0]['joint_deg'], [1, 2, 3, 4, 5, 6])
        self.assertEqual(metadata['sample_count'], len(value['samples']))
        self.assertEqual(metadata['instruction'], '抓住毛巾一角')
        self.assertFalse(metadata['training_ready'])
        count=value['sample_count']; time.sleep(.08)
        self.assertEqual(self.collector.status()['sample_count'], count)
        self.assertEqual(self.collector.stop(start['episode_id'])['sample_count'], count)
        second=self.collector.start('第二条示范')
        self.collector.stop(second['episode_id'])
        self.assertNotEqual(start['saved_dir'], second['saved_dir'])
        self.assertEqual(len(self.collector.episodes()), 2)
        self.assertTrue(all(a['t_ms']<b['t_ms'] for a,b in zip(value['samples'],value['samples'][1:])))

    def test_instruction_images_and_alignment_saved_together(self):
        # A real JPEG is used when Pillow is available; transport itself has no imaging dependency.
        try:
            from PIL import Image
            buf=BytesIO(); Image.new('RGB',(8,8),'green').save(buf,format='JPEG'); jpeg=buf.getvalue()
        except ImportError:
            jpeg=b'\xff\xd8test_transport_only\xff\xd9'
        start=self.collector.start('合爪后轻抬')
        time.sleep(.06)
        self.collector.add_frame(start['episode_id'], base64.b64encode(jpeg).decode(), 60)
        value=self.collector.stop(start['episode_id'])
        path=Path(value['saved_dir'])
        self.assertEqual((path/'instruction.txt').read_text().strip(),'合爪后轻抬')
        self.assertEqual((path/'frames/000000.jpg').read_bytes(), jpeg)
        frame=json.loads((path/'frames.jsonl').read_text())
        pair=json.loads((path/'alignment.jsonl').read_text())
        self.assertEqual(frame['timing'],'host_receipt')
        self.assertTrue(pair['matched'])
        self.assertLessEqual(abs(pair['delta_ms']), 200)
        self.assertEqual(value['frame_count'],1)
        self.assertNotIn('camera_images',json.loads((path/'metadata.json').read_text())['missing_for_training'])
        with self.assertRaises(RuntimeError): self.collector.add_frame(start['episode_id'],base64.b64encode(jpeg).decode())

    def test_disconnect_preserves_partial_data_and_marks_interrupted(self):
        start=self.collector.start('测试断流')
        time.sleep(.06);self.leader.fresh=False;time.sleep(.08)
        state=self.collector.status()
        self.assertEqual(state['state'],'interrupted')
        result=self.collector.stop(start['episode_id'])
        self.assertGreaterEqual(result['sample_count'],1)
        metadata=json.loads((Path(result['saved_dir'])/'metadata.json').read_text())
        self.assertEqual(metadata['state'],'interrupted')
        self.assertIsNotNone(metadata['error'])

    def test_rehearsals_separated_and_invalid_requests_rejected(self):
        self.leader.fresh=False
        with self.assertRaises(ValueError):self.collector.start('主臂不可用')
        for text in ['',None,'x'*501]:
            with self.assertRaises(ValueError):self.collector.start(text,'synthetic_rehearsal')
        value=self.collector.start('示例排练','synthetic_rehearsal')
        self.assertEqual(Path(value['saved_dir']).parent.name,'rehearsals')
        with self.assertRaises(ValueError):self.collector.stop('../../wrong')
        with self.assertRaises(ValueError):self.collector.add_frame(value['episode_id'],'not base64')
        self.assertEqual(self.collector.status()['state'],'recording')
        self.collector.stop(value['episode_id'])
        self.assertTrue(Path(value['saved_dir']).is_dir())

    def test_shutdown_preserves_an_interrupted_episode(self):
        start=self.collector.start('服务退出测试')
        self.collector.close()
        metadata=json.loads((Path(start['saved_dir'])/'metadata.json').read_text())
        self.assertEqual(metadata['state'],'interrupted')
        self.assertTrue((Path(start['saved_dir'])/'samples.jsonl').exists())

    def test_disk_failure_is_not_reported_as_success(self):
        from unittest.mock import patch
        start=self.collector.start('磁盘写入失败测试')
        with patch('capture.write_json', side_effect=OSError('disk full')):
            result=self.collector.stop(start['episode_id'])
        self.assertEqual(result['state'],'save_error')
        self.assertIn('disk full', result['error'])
        self.assertTrue((Path(start['saved_dir'])/'samples.jsonl').exists())


if __name__=='__main__':unittest.main()
