"""Verify the delivered SHA-256 manifest before editing target configuration."""
import hashlib, json
from pathlib import Path

def main():
    root=Path(__file__).resolve().parents[1]
    manifest=json.loads((root/'MANIFEST.json').read_text())
    failed=[]
    for row in manifest['files']:
        path=root/row['path']
        if not path.is_relative_to(root) or not path.is_file(): failed.append(row['path']); continue
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(8*1024*1024),b''): digest.update(block)
        if path.stat().st_size!=row['bytes'] or digest.hexdigest()!=row['sha256']: failed.append(row['path'])
    print(json.dumps({'checked':len(manifest['files']),'failed':failed,'ok':not failed},ensure_ascii=False))
    return bool(failed)

if __name__=='__main__': raise SystemExit(main())
