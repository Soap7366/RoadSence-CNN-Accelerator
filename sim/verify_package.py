"""Offline package integrity, naming and pinned-artifact checks. No board access."""
import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from build.package_files import package_files


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check():
    errors = []
    manifest = json.loads((ROOT/'report/package_manifest.json').read_text(encoding='utf-8'))
    for name, expected in manifest.items():
        path = ROOT/name
        if not path.is_file() or digest(path) != expected:
            errors.append('missing or changed: '+name)
    shipped=list(package_files(ROOT))
    expected=set(manifest)|{'report/package_manifest.json'}
    observed={p.relative_to(ROOT).as_posix() for p in shipped}
    for extra in sorted(observed-expected):
        errors.append('unmanifested distribution file: '+extra)
    for path in shipped:
        for part in path.relative_to(ROOT).parts:
            if part not in ('README.md', 'SKILL.md', 'Start.exe', 'Prepare.exe', 'Prepare.cmd') and not re.fullmatch('[a-z0-9_.-]+',part):
                errors.append('invalid name: '+str(path.relative_to(ROOT)))
        if path.suffix == '.py':
            try:
                ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
            except (SyntaxError, UnicodeError) as exc:
                errors.append(str(exc))
        if path.suffix=='.sh' and b'\r' in path.read_bytes():
            errors.append('shell requires LF: '+str(path.relative_to(ROOT)))
    overlay = ROOT/'board/base/overlays/cnn_v1'
    spec = json.loads((overlay/'overlay_manifest.json').read_text())
    for info in spec['files'].values():
        path = overlay/info['path']
        if digest(path) != info['sha256'] or path.stat().st_size != info['bytes']:
            errors.append('overlay mismatch: '+info['path'])
    for folder in ['src/stream/config','board/base/config','data/model']:
        path = ROOT/folder
        contract = json.loads((path/'model_contract.json').read_text())
        if digest(path/'quant_params.json') != contract['quant_params_sha256']:
            errors.append('quant mismatch: '+folder)
    golden = ROOT/'board/base/fixtures/hardware_golden_30'
    samples = json.loads((golden/'manifest.json').read_text())['samples']
    if len(samples) != 30:
        errors.append('expected 30 golden samples')
    for sample in samples:
        path = golden/sample['input_file']
        if path.stat().st_size != 4096 or digest(path) != sample['input_sha256']:
            errors.append('golden input mismatch: '+sample['input_file'])
    vectors = ROOT/'src/core/data/dual_task_baseline2_qat'
    fixtures = json.loads((vectors/'manifest.json').read_text())['fixture_sha256']
    for name, expected in fixtures.items():
        if digest(vectors/name) != expected:
            errors.append('RTL fixture mismatch: '+name)
    integration=ROOT/'src/integration'
    upstream=integration/'upstream/systoliccnn_8c8dcfafc901f0c54276e6aaba86d817cb82538d'
    provenance=json.loads((integration/'reports/source_provenance.json').read_text())
    for name, expected in provenance['files'].items():
        path=upstream/name
        if path.stat().st_size!=expected['bytes'] or digest(path)!=expected['sha256']:
            errors.append('pinned integration source mismatch: '+name)
    presets=integration/'config/boards'
    for name,expected in json.loads((presets/'source_manifest.json').read_text())['files'].items():
        if digest(presets/name)!=expected['sha256']:
            errors.append('board preset/license mismatch: '+name)
    return dict(status='FAIL' if errors else 'PASS', checked_files=len(manifest), errors=errors,
                scope='offline integrity and syntax; no hardware validation')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output')
    args = parser.parse_args()
    report = check()
    text = json.dumps(report,indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text,encoding='utf-8')
    raise SystemExit(report['status'] != 'PASS')
