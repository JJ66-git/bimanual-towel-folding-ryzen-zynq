#!/usr/bin/env python3
"""Deploy the paired visual UI release to the selected B35 runtime; no robot access."""
import argparse,hashlib,json
from pathlib import Path
from jupyter_client import JupyterBoard
from control_jupyter import request,PROFILE
ROOT=Path(__file__).resolve().parents[2]
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--source',choices=['host_observation','synthetic_test'],default='host_observation');parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
 profile=json.loads(PROFILE.read_text());assert profile['expected_build_id']=='0x46504735'
 archive=ROOT/'npu_deploy/artifacts/fpga_board/releases/b35_cooperative_ui_20261113.zip';digest=hashlib.sha256(archive.read_bytes()).hexdigest()
 remote='fpga_b35_handoff_20261113/'+archive.name;b=JupyterBoard()
 if not b.authenticated():b.login_dialog()
 b.upload(remote,archive.read_bytes());state=request('stop');assert state['state']=='stopped'
 code='''from pathlib import Path
import hashlib,json,zipfile
root=Path(%r);archive=Path('/home/xilinx/jupyter_notebooks')/%r
assert hashlib.sha256(archive.read_bytes()).hexdigest()==%r
with zipfile.ZipFile(archive) as z:
 m=json.loads(z.read('release_manifest.json'));assert m['build_id']=='0x46504735'
 assert m['ui_revision']=='cooperative_ui_20261113_1'
 assert all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in z.namelist())
 for row in m['files']:assert hashlib.sha256(z.read(row['path'])).hexdigest()==row['sha256']
 z.extractall(root)
for row in m['files']:assert hashlib.sha256((root/row['path']).read_bytes()).hexdigest()==row['sha256']
print('UI_FILES_VERIFIED='+str(len(m['files'])))
'''%(profile['board_root'],remote,digest)
 result=b.execute(code);args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,indent=2)+'\n');print(result['output']);assert result['passed'],result['errors']
 print('UI_SERVICE='+json.dumps(request('start',source=args.source)))
if __name__=='__main__':main()
