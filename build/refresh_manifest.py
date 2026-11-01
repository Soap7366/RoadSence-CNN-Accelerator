"""Maintainer action: refresh the distribution manifest after reviewed source edits."""
import hashlib
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from build.package_files import package_files

if __name__=='__main__':
    manifest={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
              for p in package_files(ROOT) if p.relative_to(ROOT).as_posix()!='report/package_manifest.json'}
    (ROOT/'report/package_manifest.json').write_bytes(json.dumps(manifest,indent=2,sort_keys=True).encode('utf-8'))
    print('Manifest refreshed:',len(manifest),'distribution files')
