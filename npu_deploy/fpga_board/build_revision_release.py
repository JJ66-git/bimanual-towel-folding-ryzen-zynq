"""Package the isolated B35 source, firmware and B34 rollback; never credentials."""
import argparse,hashlib,json,shutil,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'fpga_integration/release_candidates/fpg1_v3_b35_20261113'
BASE=ROOT/'fpga_integration/fpga_code/fpga_workspace'
OUT=ROOT/'npu_deploy/artifacts/fpga_board/releases'
TAG='fpg1_v3_b35_20261113_revision'

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--name',default='b35_cooperative_ui_20261113');args=parser.parse_args()
 if not args.name.replace('_','').isalnum():raise ValueError('Unsafe archive name')
 rows=[];files={}
 for path in sorted(SOURCE.rglob('*')):
  rel=path.relative_to(SOURCE)
  if not path.is_file() or any(p in ('validation_runs','__pycache__','.Xil') for p in rel.parts) or path.suffix in ('.log','.jou','.str','.pyc','.vvp'):continue
  files[str(rel)]=path
 old=BASE/'overlay/lcd_framebuffer_fpg1_v3_b34_20261113_final'
 for name in ['fpg1_v3_b34_20261113_final.bit','fpg1_v3_b34_20261113_final.hwh']:
  files['rollback/'+name]=old/name
 for suffix in ['bit','hwh']:
  assert (SOURCE/'overlay'/('lcd_framebuffer_'+TAG)/(TAG+'.'+suffix)).is_file(),'Build not complete'
 for relative,path in files.items():
  data=path.read_bytes();rows.append({'path':relative,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
 manifest={'release':'fpg1_v3_b35_20261113','build_id':'0x46504735','tool':'Vivado 2018.3 WebPACK','target':'xc7z020clg400-2',
 'qualification':'B35 with event-driven cooperative UI; field camera calibration remains pending','ui_revision':'cooperative_ui_20261113_1','robot_motion':False,
 'phase_config':json.loads((SOURCE/'config/fold_verify_identity.json').read_text()),'files':rows}
 OUT.mkdir(parents=True,exist_ok=True);archive=OUT/(args.name+'.zip')
 with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as output:
  for relative,path in files.items():output.write(path,relative)
  output.writestr('release_manifest.json',json.dumps(manifest,indent=2)+'\n')
 (OUT/(args.name+'_manifest.json')).write_text(json.dumps(manifest,indent=2)+'\n')
 (OUT/(args.name+'_SHA256SUMS')).write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
 print('B35_RELEASE='+str(archive));print('FILES='+str(len(rows)))
if __name__=='__main__':main()
