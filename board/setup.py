"""PYNQ-Z2 environment preflight, native build/tests and explicit overlay boot."""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def preflight():
    if platform.system()!='Linux' or platform.machine()!='armv7l':
        raise RuntimeError('Hardware target is Linux ARMv7 PYNQ-Z2; PC workflows run through manage.py')
    import pynq
    import numpy
    import PIL
    if PIL.__version__!='9.0.1':
        raise RuntimeError('Reference preprocessing requires Pillow 9.0.1 in the PYNQ environment; observed '+PIL.__version__)
    if not shutil.which('gcc'):raise RuntimeError('Install board prerequisites: sudo apt-get install build-essential libjpeg-dev')
    return dict(status='PASS',machine=platform.machine(),python=sys.version,
                pynq=getattr(pynq,'__version__','unknown'),numpy=numpy.__version__,pillow=PIL.__version__)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build',action='store_true');parser.add_argument('--boot',action='store_true')
    args=parser.parse_args();report=preflight()
    subprocess.run([sys.executable,'-B',str(ROOT/'sim/verify_package.py')],check=True)
    if args.build:
        subprocess.run(['sh',str(ROOT/'build/build_native.sh')],check=True)
        subprocess.run([sys.executable,'-B','-m','unittest','discover','-s','tests'],cwd=ROOT/'src/stream',check=True)
    report['booted']=False
    if args.boot:
        if os.geteuid()!=0:raise RuntimeError('Overlay boot requires sudo')
        env=dict(os.environ,XILINX_XRT='/usr',BOARD='Pynq-Z2',PYTHONDONTWRITEBYTECODE='1')
        subprocess.run([sys.executable,'-B',str(ROOT/'board/base/tools/boot_pl.py'),
                        '--manifest',str(ROOT/'board/base/overlays/cnn_v1/overlay_manifest.json'),
                        '--state','/run/pscnn/boot_state.json','--timeout','60'],check=True,env=env)
        report['booted']=True
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
