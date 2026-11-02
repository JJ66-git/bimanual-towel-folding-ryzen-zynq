import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image
from screen.view import ScreenState

ROOT=Path(__file__).resolve().parents[1]


class ScreenReplayTests(unittest.TestCase):
    def test_query_and_unmatched_reply_never_become_a_completed_check(self):
        state=ScreenState()
        state.apply(dict(event='status',violations=0xffffffff,sequence=0))
        self.assertIsNone(state.summary()['last_check'])
        state.apply(dict(event='session_started',session=7))
        state.apply(dict(event='action_observed',session=7,sequence=4,violations=0))
        state.apply(dict(event='diagnostic_reply_sent',sequence=5))
        self.assertIsNone(state.summary()['reply_sequence'])
        state.apply(dict(event='diagnostic_reply_sent',sequence=4))
        self.assertEqual(state.summary()['reply_sequence'],4)
        state.apply(dict(event='check_reply_sent',session=8,sequence=0,violations=1))
        state.apply(dict(event='diagnostic_reply_sent',sequence=4))
        self.assertEqual(state.summary()['reply_sequence'],0)
        self.assertEqual(state.summary()['last_check']['namespace'],'FPG1')
        self.assertEqual(state.summary()['reasons'],['TARGET RANGE'])

    def test_recorded_board_log_renders_portrait_and_traceable_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            out=Path(temporary)/'screen.png'
            result=subprocess.run([sys.executable,'-B','-m','screen.replay',
                '--log',str(ROOT/'tests/fixtures/screen_events.jsonl'),
                '--manifest',str(ROOT/'tests/fixtures/screen_manifest.json'),
                '--out',str(out)],cwd=ROOT,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            with Image.open(out) as image:
                self.assertEqual(image.size,(480,800))
            data=json.loads(out.with_suffix('.json').read_text())
            self.assertEqual((data['checked'],data['flagged'],data['rejected']),(5,0,10))
            self.assertEqual((data['last_check']['sequence'],data['reply_sequence']),(11,11))
            self.assertEqual(data['watchdog']['timeout_count'],1)
            self.assertTrue(data['service_stopped'])
            self.assertEqual(data['display_mode'],'REPLAY')


if __name__=='__main__':
    unittest.main()
