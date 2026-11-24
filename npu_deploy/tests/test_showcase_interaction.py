"""Exercise the native-panel bridge using in-memory responses, never sockets."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from showcase_interaction import InteractionBridge,BUILD_ID


class InteractionTests(unittest.TestCase):
    def reply(self,value,**overrides):
        return dict(source='board',build_id=BUILD_ID,reference_match=True,value=value/1000,
                    mask=int(value>2540),seq=1,session=2,**overrides)
    def test_old_response_cannot_replace_new_slider_position(self):
        def request(path,data):
            if data['millirad']==2650:bridge.preview_angle(2540)
            return self.reply(data['millirad'])
        bridge=InteractionBridge(request);bridge.angle(2650);generation=bridge.generation
        bridge._check_angle((generation,2650))
        self.assertIsNone(bridge.snapshot()['result'])
        bridge.angle(2540);bridge._check_angle((bridge.generation,2540))
        self.assertEqual(bridge.snapshot()['result']['mask'],0)
    def test_dragging_immediately_clears_the_previous_pass(self):
        bridge=InteractionBridge(lambda path,data:self.reply(data['millirad']))
        bridge.angle(2540);bridge._check_angle((bridge.generation,2540))
        bridge.preview_angle(2650)
        self.assertIsNone(bridge.snapshot()['result'])
        self.assertEqual(bridge.snapshot()['requested_millirad'],2650)
    def test_wrong_build_or_reference_mismatch_is_never_accepted(self):
        for field,value in [('build_id',0x46504734),('source','reference'),('reference_match',False),('value',2.54)]:
            with self.subTest(field=field):
                result=self.reply(2650);result[field]=value
                bridge=InteractionBridge(lambda path,data:result);bridge.angle(2650)
                with self.assertRaises(ValueError):bridge._check_angle((bridge.generation,2650))
                self.assertIsNone(bridge.snapshot()['result'])
    def test_software_mode_cannot_enable_real_fpga_controls(self):
        def request(path):
            if path=='/mode':return dict(source='reference',protocol='v3',expected_build_id=BUILD_ID)
            return {}
        bridge=InteractionBridge(request);bridge._refresh();self.assertFalse(bridge.snapshot()['ready'])
    def test_power_confirmation_checks_live_signal_then_starts_real_capture(self):
        leader = dict(ready=False)
        responses = {
            '/mode': dict(source='board', protocol='v3', expected_build_id=BUILD_ID, leader_enabled=True),
            '/leader/status': leader,
            '/capture/status': dict(state='idle'),
            '/capture/start': dict(state='recording', source='piper_leader_commands'),
        }
        calls = []

        def request(path, payload=None):
            calls.append((path, payload))
            return responses[path]

        bridge = InteractionBridge(request)
        bridge._refresh()

        self.assertNotIn(('/leader/status', None), calls)
        calls.clear()
        bridge.capture_start('test instruction')
        bridge._capture_action(*bridge.actions.popleft())
        self.assertEqual(calls, [('/leader/status', None)])
        self.assertFalse(bridge.snapshot()['capture_busy'])
        self.assertIn('未收到完整的主臂信号', bridge.snapshot()['capture_error'])
        self.assertEqual(bridge.snapshot()['capture']['state'], 'idle')

        leader['ready'] = True
        calls.clear()
        bridge.capture_start('test instruction')
        bridge.capture_start('second click')
        self.assertEqual(len(bridge.actions), 1)
        bridge._capture_action(*bridge.actions.popleft())
        self.assertEqual(calls, [('/leader/status', None), ('/capture/start', dict(
            instruction='test instruction', source='piper_leader_commands'))])
        self.assertEqual(bridge.snapshot()['capture']['state'], 'recording')
        self.assertFalse(bridge.snapshot()['capture_busy'])
        self.assertEqual(bridge.snapshot()['capture_error'], '')

    def test_power_confirmation_reports_missing_leader_interface(self):
        responses = {
            '/mode': dict(source='board', protocol='v3', expected_build_id=BUILD_ID, leader_enabled=False),
            '/capture/status': dict(state='idle'),
        }
        bridge = InteractionBridge(responses.__getitem__)
        bridge._refresh()
        bridge.capture_start('test instruction')
        bridge._capture_action(*bridge.actions.popleft())
        self.assertIn('右主臂 CAN 接口尚未配置', bridge.snapshot()['capture_error'])
        self.assertEqual(bridge.snapshot()['capture']['state'], 'idle')


if __name__=='__main__':unittest.main()
