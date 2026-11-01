"""Export to a fresh temporary directory and reinstall offline; never copy a venv."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import manage


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python',required=True,help='Healthy base interpreter, not an existing venv')
    args=parser.parse_args()
    container=Path(tempfile.mkdtemp(prefix='pynq_repro_'))
    package=container/'portable_project'
    commands=[
        [sys.executable,str(ROOT/'manage.py'),'export','--output',str(package)],
        [sys.executable,str(package/'manage.py'),'setup','--python',args.python,'--offline',
         '--wheelhouse',str(ROOT/'.runtime/wheels')],
        [sys.executable,str(package/'manage.py'),'test'],
        [sys.executable,str(package/'manage.py'),'doctor','--gui'],
    ]
    # Intentionally unrelated cwd; clean environment removes old-project injection.
    logs=[]
    status='PASS'
    for command in commands:
        result=subprocess.run(command,cwd=container,env=manage.clean_env(),stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT,encoding='utf-8',errors='replace',timeout=300)
        logs.append(dict(command=command,exit_code=result.returncode,output=result.stdout))
        print(result.stdout,flush=True)
        if result.returncode:status='FAIL';break
    report=dict(status=status,exported_to=str(package),same_platform=True,
                copied_virtual_environment=False,shared_wheel_cache_only=True,steps=logs)
    output=ROOT/'sim/results';output.mkdir(exist_ok=True)
    (output/'relocation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('Relocation rehearsal:',status,package)
    return status!='PASS'


if __name__=='__main__':raise SystemExit(main())
