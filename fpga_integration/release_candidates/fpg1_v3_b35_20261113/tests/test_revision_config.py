import copy,json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from host import fold_config,fold_protocol as wire
from host.reference import load_profile
from board.fold_bridge import FoldPynqGuard
class ConfigurationTests(unittest.TestCase):
 def test_canonical_order_and_no_newline(self):
  p=fold_config.load();self.assertEqual(fold_config.identity(p),fold_config.identity(dict(reversed(list(p.items())))))
  self.assertFalse(fold_config.canonical(p).endswith(b'\n'))
 def test_float_rejected(self):
  p=fold_config.load();p['vision']['center_delta_px']=8.0
  with self.assertRaises(ValueError):fold_config.canonical(p)
 def test_duplicate_key_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'bad.json';path.write_text('{"schema":1,"schema":2}')
   with self.assertRaises(ValueError):fold_config.load(path)
 def test_no_field_calibration_promotion(self):
  p=fold_config.load();p['calibration_status']='field_validated'
  with self.assertRaises(ValueError):fold_config.validate(p)
 def test_header_matches_identity_and_threshold(self):
  p=fold_config.load();header=fold_config.header(p)
  self.assertIn("32'h%08x"%fold_config.identity(p)['crc32'],header)
  p['vision']['center_delta_px']=7
  self.assertIn('FV_VISION_CENTER_DELTA_PX 32\'sd7',fold_config.header(p))
 def test_pl_crc_mismatch_rejected(self):
  class IP:
   def read(self,offset):return wire.BUILD_ID if offset==0 else 0
  with self.assertRaisesRegex(RuntimeError,'PHASE_CONFIG_CRC'):FoldPynqGuard(IP(),load_profile())
 def test_old_build_rejected(self):
  crc=fold_config.identity(fold_config.load())['crc32']
  class IP:
   def read(self,offset):return crc if offset==0x98 else 0x46504734
  with self.assertRaisesRegex(RuntimeError,'BUILD_ID'):FoldPynqGuard(IP(),load_profile())
 def test_snapshot_layout(self):
  crc=fold_config.identity(fold_config.load())['crc32']
  class IP:
   def read(self,offset):return {0:wire.BUILD_ID,0x98:crc,8:2,0xcc:0x80030002}.get(offset,0)
   def write(self,*args):pass
  guard=FoldPynqGuard(IP(),load_profile())
  frame=wire.Frame(b'FVE1',1,1,1,2,500,(100,3,4290,300,200,9000,1500,1))
  self.assertEqual(wire.FVE1_RESULT.unpack(guard.feature(frame)),(3,2,0,3))
if __name__=='__main__':unittest.main()
