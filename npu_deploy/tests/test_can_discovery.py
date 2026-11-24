"""Identity diagnostics use a temporary sysfs tree; no CAN frames or link changes."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/'amd_hybrid_deploy_20261031/piper_towel_replica/scripts/prepare_cobot.py'
spec=importlib.util.spec_from_file_location('can_discovery_subject',SCRIPT)
subject=importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


class CanDiscoveryTests(unittest.TestCase):
    def test_inventory_reads_serial_from_usb_parent_without_device_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);net=root/'net';net.mkdir()
            usb=root/'usb'/'3-3.1';device=usb/'3-3.1:1.0';device.mkdir(parents=True)
            (usb/'serial').write_text('adapter-A\n')
            port=net/'can0';port.mkdir();(port/'device').symlink_to(device)
            rows=subject.can_inventory(net)
            self.assertEqual([(r['interface'],r['serial']) for r in rows],[('can0','adapter-A')])

    def test_unbound_site_reports_detected_adapters_without_querying_cameras(self):
        rows=[dict(interface='can0',serial='adapter-A'),dict(interface='can1',serial='adapter-B')]
        camera_scan=unittest.mock.Mock(side_effect=AssertionError('must not scan cameras'))
        camera_module=SimpleNamespace(scan_cameras=camera_scan,match_roles=None)
        site={'usb_serials':{'left':'REPLACE_USB_SERIALS_LEFT','right':'REPLACE_USB_SERIALS_RIGHT'}}
        with patch.object(subject,'can_inventory',return_value=rows),patch.dict(sys.modules,{'camera_inventory':camera_module}):
            with self.assertRaises(ValueError) as caught:subject.discover(site)
        message=str(caught.exception)
        self.assertIn('placeholders',message)
        self.assertIn('adapter-A',message)
        self.assertIn('adapter-B',message)
        self.assertIn('matched={}',message)
        camera_scan.assert_not_called()

    def test_unknown_serial_is_never_matched_to_a_missing_saved_identity(self):
        camera_module=SimpleNamespace(scan_cameras=None,match_roles=None)
        site={'usb_serials':{'left':None,'right':None}}
        with patch.object(subject,'can_inventory',return_value=[dict(interface='can0',serial=None)]),patch.dict(sys.modules,{'camera_inventory':camera_module}):
            with self.assertRaisesRegex(ValueError,'matched=\\{\\}'):subject.discover(site)


if __name__=='__main__':unittest.main()
