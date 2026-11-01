"""Run isolated suites with the active interpreter; ARM tests are reported skipped."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    jobs = [
        ('skill',ROOT,[sys.executable,'-B','-m','unittest','discover','-s','sim','-p','test_skill.py']),
        ('automation',ROOT,[sys.executable,'-B','-m','unittest','discover','-s','sim','-p','test_automation.py']),
        ('stream',ROOT/'src/stream',[sys.executable,'-B','-m','unittest','discover','-s','tests']),
        ('desktop',ROOT/'src/desktop',[sys.executable,'-B','test_board_adapter.py']),
    ]
    output=ROOT/'sim/results';output.mkdir(exist_ok=True)
    results=[]
    for name,cwd,command in jobs:
        result=subprocess.run(command,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                              encoding='utf-8',errors='replace',env=dict(os.environ,PYTHONIOENCODING='utf-8'),timeout=180)
        (output/(name+'.log')).write_text(result.stdout,encoding='utf-8')
        results.append(dict(suite=name,exit_code=result.returncode,command=command,output=result.stdout))
        print(name+': '+('PASS' if result.returncode==0 else 'FAIL'))
    report=dict(status='PASS' if all(r['exit_code']==0 for r in results) else 'FAIL',
                python=sys.version,scope='offline and software mock; no PL, GUI or Vivado execution',suites=results)
    (output/'offline_validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report['status']!='PASS'


if __name__=='__main__':
    raise SystemExit(main())
