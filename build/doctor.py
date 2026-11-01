"""Check the active isolated environment; optionally create a real Tk window."""
import argparse
import importlib.metadata
import json
import platform
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def check(gui=False):
    import cv2
    import numpy
    import PIL
    import paramiko
    import tkinter
    modules=[cv2,numpy,PIL,paramiko]
    env=ROOT/'.runtime/pc_env'
    if Path(sys.prefix).resolve()!=env.resolve():raise RuntimeError('Use the package isolated environment')
    for module in modules:
        if env.resolve() not in Path(module.__file__).resolve().parents:
            raise RuntimeError('External dependency detected: '+str(module.__file__))
    tcl=tkinter.Tcl().eval('info patchlevel')
    if gui:
        window=tkinter.Tk();window.withdraw();window.update();window.destroy()
    report=dict(status='PASS',platform=platform.platform(),python=sys.version,
                dependencies={n:importlib.metadata.version(n) for n in ('numpy','Pillow','opencv-python','paramiko','cryptography')},
                module_paths={m.__name__:str(m.__file__) for m in modules},
                tcl=tcl,gui_window_created=gui,external_project_dependencies=False)
    target=ROOT/'sim/results';target.mkdir(exist_ok=True)
    (target/'environment.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--gui',action='store_true')
    check(parser.parse_args().gui)
