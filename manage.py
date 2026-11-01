"""Portable entry point. Run `python manage.py --help` from any directory."""
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / '.runtime'
ENV = RUNTIME / 'pc_env'
PYTHON = ENV / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
LOCK = ROOT / 'build/requirements_pc.txt'


def clean_env():
    env = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'PSCNN_SSH_VENDOR', 'PSCNN_KNOWN_HOSTS',
                'PSCNN_REMOTE_ROOT', 'TCL_LIBRARY', 'TK_LIBRARY'):
        env.pop(key, None)
    env.update(PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    env['PIP_CACHE_DIR']=str(RUNTIME/'pip_cache')
    # A venv uses its base interpreter's standard Tcl, never another project.
    cfg = ENV / 'pyvenv.cfg'
    if cfg.exists() and os.name == 'nt':
        match = re.search(r'^home\s*=\s*(.+)$', cfg.read_text(), re.M)
        if match:
            base = Path(match[1].strip())
            for key, sub in [('TCL_LIBRARY', 'tcl8.6'), ('TK_LIBRARY', 'tk8.6')]:
                for tcl in (base/'tcl'/sub, base/'Library/lib'/sub):
                    if tcl.is_dir():
                        env[key] = str(tcl)
                        break
            if (base/'Library/bin').is_dir():
                env['PATH'] = str(base/'Library/bin') + os.pathsep + env.get('PATH','')
    return env


def run(command, cwd=ROOT, **kwargs):
    print('+ ' + ' '.join(str(arg) for arg in command), flush=True)
    subprocess.run([str(arg) for arg in command], cwd=cwd, env=clean_env(), check=True, **kwargs)


def require_environment():
    if not PYTHON.is_file():
        raise RuntimeError('Run python manage.py setup first (Python 3.9-3.12 with Tcl/Tk).')


def runtime(command):
    require_environment()
    run([PYTHON, '-B', *command])


def config_module():
    sys.path.insert(0, str(ROOT/'src/stream'))
    from pscnn import runtime_config
    return runtime_config


def setup(args):
    interpreter = args.python or sys.executable
    probe = "import sys; assert (3,9)<=sys.version_info[:2]<=(3,12), 'Use Python 3.9-3.12'; import tkinter; print(sys.version); print('Tcl', tkinter.Tcl().eval('info patchlevel'))"
    try:
        run([interpreter, '-I', '-c', probe])
    except subprocess.CalledProcessError:
        raise RuntimeError('Base Python/Tcl is incomplete. Install standard Python with Tk, or use setup --python /path/to/healthy/python. No old-project fallback is used.')
    if not PYTHON.exists():
        run([interpreter, '-I', '-m', 'venv', ENV])
    source = Path(args.wheelhouse).resolve() if args.wheelhouse else RUNTIME/'wheels'
    source.mkdir(parents=True, exist_ok=True)
    if not args.offline:
        run([PYTHON, '-I', '-m', 'pip', '--isolated', '--cache-dir', RUNTIME/'pip_cache', 'download', '--only-binary=:all:',
             '--dest', source, '-r', LOCK, '--index-url', 'https://pypi.org/simple'])
    saved=source/'wheel_manifest.json'
    if saved.exists():
        for name,expected in json.loads(saved.read_text(encoding='utf-8')).items():
            wheel=source/name
            if not wheel.is_file() or hashlib.sha256(wheel.read_bytes()).hexdigest()!=expected:
                raise RuntimeError('Wheel cache integrity mismatch: '+name)
    run([PYTHON, '-I', '-m', 'pip', '--isolated', '--cache-dir', RUNTIME/'pip_cache', 'install', '--no-index', '--find-links',
         source, '-r', LOCK])
    run([PYTHON, '-I', '-m', 'pip', 'check'])
    if not (ROOT/'board/local_config.json').exists():
        (ROOT/'board/local_config.json').write_bytes((ROOT/'board/config.example.json').read_bytes())
    wheels = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(source.glob('*.whl'))}
    (RUNTIME/'wheel_manifest.json').write_text(json.dumps(wheels, indent=2), encoding='utf-8')
    (source/'wheel_manifest.json').write_text(json.dumps(wheels,indent=2),encoding='utf-8')
    runtime(['build/doctor.py'])


def configure(args):
    mod = config_module()
    values = mod.load_config()
    values.pop('known_hosts', None)
    for key in ('host', 'username', 'port', 'remote_base', 'remote_python'):
        value = getattr(args, key, None)
        if value is not None:
            values[key] = value
    remote = values['remote_base']
    if not re.fullmatch(r'/home/[a-z_][a-z0-9_-]*/[a-z0-9_-]+', remote):
        raise ValueError('remote_base must be /home/<user>/<new_project_name>')
    values['remote_root'] = remote
    mod.save_config(values)
    print(json.dumps(values, indent=2))


def platform_check(args):
    require_environment()
    output=ROOT/'sim/results';output.mkdir(exist_ok=True)
    for target in ('manylinux2014_x86_64','macosx_13_0_x86_64','macosx_13_0_arm64'):
        run([PYTHON,'-I','-m','pip','--isolated','--cache-dir',RUNTIME/'pip_cache','install','--dry-run','--ignore-installed',
             '--only-binary=:all:','--platform',target,'--python-version','312',
             '--implementation','cp','--abi','cp312','--report',output/('resolve_'+target+'.json'),
             '-r',LOCK,'--index-url','https://pypi.org/simple'])


def dependency_command(args):
    # Relaunch in the isolated environment before importing Paramiko.
    if Path(sys.prefix).resolve() != ENV.resolve():
        require_environment()
        run([PYTHON, '-B', Path(__file__), *sys.argv[1:]])
        return
    from build import remote_control
    getattr(remote_control, args.command.replace('-', '_'))(args)


def rtl(args):
    bin_dir = Path(args.vivado_bin) if args.vivado_bin else None
    def tool(name):
        suffix = '.bat' if os.name == 'nt' else ''
        value = bin_dir/(name+suffix) if bin_dir else shutil.which(name+suffix)
        if not value or not Path(value).is_file():
            raise RuntimeError('Vivado tool missing: '+name+'; specify --vivado-bin')
        return str(value)
    source = ROOT/'src/core'
    core = RUNTIME/('r_'+uuid.uuid4().hex[:8])
    core.mkdir(parents=True)
    for folder in ('rtl','sim','data','constraints','scripts'):
        shutil.copytree(source/folder,core/folder)
    output = core/'build'; output.mkdir(exist_ok=True)
    if args.action == 'implement':
        run([tool('vivado'), '-mode','batch','-source', ROOT/'build/implement_core.tcl',
             '-log', output/'implementation.log','-journal',output/'implementation.jou',
             '-tclargs', core],cwd=core)
        return
    for name in args.tests:
        if name not in ('network','gap','linear','control','array','post','memory','layer'):
            raise ValueError('Unknown test '+name)
        sources = sorted((core/'rtl').glob('*.v'))
        run([tool('xvlog'),'--sv',*sources,core/'sim'/('tb_'+name+'.sv'),
             '--log', output/('xvlog_'+name+'.log')],cwd=core)
        run([tool('xelab'),'tb_'+name,'-s','sim_'+name,'--debug','typical',
             '--log',output/('xelab_'+name+'.log')],cwd=core)
        log = output/('xsim_'+name+'.log')
        run([tool('xsim'),'sim_'+name,'-runall','--log',log],cwd=core)
        text = log.read_text(errors='replace')
        if not re.search(r'^PASS tb_'+name+r'\b', text, re.M) or re.search(r'^(Fatal:|ERROR:)',text,re.M):
            raise RuntimeError('RTL test failed: '+name)
        evidence=ROOT/'sim/results';evidence.mkdir(exist_ok=True)
        shutil.copyfile(log,evidence/('rtl_'+name+'.log'))


def export(args):
    from build.package_files import package_files
    destination = Path(args.output).resolve()
    if ROOT == destination or ROOT in destination.parents:
        raise ValueError('Export destination must be outside the project folder')
    if destination.exists():
        raise ValueError('Use a new export directory')
    destination.mkdir(parents=True)
    for path in package_files(ROOT):
        target = destination/path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path,target)
    print('Exported portable source package:',destination)


def overlay(args):
    """Recreate the pinned AXI/PS7 build workspace, including its test data."""
    if args.action=='build' and set(args.tests)!={'bridge','network'}:
        raise ValueError('Full overlay build requires both bridge and network simulations')
    if args.action=='build' and os.name=='nt':
        RUNTIME.mkdir(exist_ok=True)
        probe=RUNTIME/('cscript_'+uuid.uuid4().hex[:8]+'.js')
        probe.write_text('WScript.Quit(0);',encoding='ascii')
        try:
            run(['cscript.exe','//nologo','//E:JScript',probe])
        except subprocess.CalledProcessError:
            raise RuntimeError('Vivado launch_runs requires Windows Script Host access; run outside a restricted sandbox or fix the system policy before building.')
        finally:
            probe.unlink()
    source=ROOT/'src/integration'
    workspace=Path(args.workspace).resolve() if args.action=='finish' and args.workspace else RUNTIME/('o_'+uuid.uuid4().hex[:8])
    if args.action=='finish' and (not args.workspace or RUNTIME.resolve() not in workspace.parents):
        raise ValueError('finish requires --workspace pointing inside this package .runtime')
    print('Overlay workspace:',workspace,flush=True)
    if args.action!='finish':shutil.copytree(source,workspace)
    else:shutil.copyfile(source/'scripts/finish_overlay.tcl',workspace/'scripts/finish_overlay.tcl')
    if args.action=='finish' and (workspace/'artifacts/overlay_manifest.json').exists():
        previous=workspace/'artifacts'/('overlay_manifest_previous_'+uuid.uuid4().hex[:8]+'.json')
        (workspace/'artifacts/overlay_manifest.json').rename(previous)
    upstream=workspace/'upstream/systoliccnn_8c8dcfafc901f0c54276e6aaba86d817cb82538d'
    shutil.copytree(ROOT/'src/core/data',upstream/'data',dirs_exist_ok=True)
    # Restore exact upstream files pinned by source_provenance (including line endings).
    shutil.copytree(source/'upstream',workspace/'upstream',dirs_exist_ok=True)
    bin_dir=Path(args.vivado_bin) if args.vivado_bin else None
    def tool(name):
        name+=('.bat' if os.name=='nt' else '')
        value=bin_dir/name if bin_dir else shutil.which(name)
        if not value or not Path(value).is_file():raise RuntimeError('Missing Vivado tool: '+name)
        return str(value)
    if args.action in ('sim','build'):
        for name in args.tests:
            if name not in ('bridge','network'):raise ValueError('Unknown AXI test')
            cwd=workspace/'build'/('sim_'+name);cwd.mkdir(parents=True,exist_ok=True)
            sources=[workspace/'rtl/cnn_axi_lite_bridge.sv']
            if name=='network':sources+=sorted((upstream/'rtl').glob('*.v'))+[workspace/'rtl/cnn_axi_accelerator.sv']
            run([tool('xvlog'),'--sv',*sources,workspace/'sim'/('tb_axi_'+name+'.sv'),'--log','xvlog.log'],cwd=cwd)
            run([tool('xelab'),'tb_axi_'+name,'-s','sim_axi_'+name,'--debug','typical','--log','xelab.log'],cwd=cwd)
            run([tool('xsim'),'sim_axi_'+name,'-runall','--log','xsim.log'],cwd=cwd)
            text=(cwd/'xsim.log').read_text(errors='replace')
            if 'PASS tb_axi_'+name not in text or re.search(r'^(Fatal:|ERROR:)',text,re.M):
                raise RuntimeError('AXI test failed: '+name)
            target=ROOT/'sim/results';target.mkdir(exist_ok=True)
            shutil.copyfile(cwd/'xsim.log',target/('axi_'+name+'.log'))
    if args.action in ('check','build','finish'):
        script='finish_overlay.tcl' if args.action=='finish' else 'build_overlay.tcl'
        options=[workspace] if args.action=='finish' else [workspace,args.action]
        run([tool('vivado'),'-mode','batch','-source',workspace/'scripts'/script,
             '-log',workspace/'vivado.log','-journal',workspace/'vivado.jou',
             '-tclargs',*options],cwd=workspace)
    if args.action in ('build','finish'):
        if (workspace/'artifacts/overlay_manifest.json').exists():
            raise RuntimeError('A manifest already exists; use a new build workspace to regenerate')
        run([sys.executable,'-B',workspace/'scripts/make_overlay_manifest.py'],cwd=workspace)
        target=ROOT/'sim/results';target.mkdir(exist_ok=True)
        (target/'overlay_build.json').write_text(json.dumps(dict(status='PASS',workspace=str(workspace),
            artifacts=str(workspace/'artifacts'),hardware_verified=False,
            manifest=json.loads((workspace/'artifacts/overlay_manifest.json').read_text())),indent=2),encoding='utf-8')
    print('Overlay workspace:',workspace)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('setup',help='Create isolated PC environment; install pinned wheels')
    p.add_argument('--python');p.add_argument('--offline',action='store_true');p.add_argument('--wheelhouse')
    p=sub.add_parser('doctor');p.add_argument('--gui',action='store_true')
    sub.add_parser('verify');sub.add_parser('test');sub.add_parser('start')
    sub.add_parser('platform-check',help='Resolve Linux/macOS pinned wheels; does not execute those operating systems')
    p=sub.add_parser('configure')
    for name in ('host','username','remote-base','remote-python'):p.add_argument('--'+name)
    p.add_argument('--port',type=int)
    p=sub.add_parser('trust',help='Pin host key only when its SHA256 matches independently verified fingerprint')
    p.add_argument('--fingerprint',required=True)
    p=sub.add_parser('deploy',help='Upload a new immutable release; build/check; activate only on success')
    p.add_argument('--overlay',help='Optional newly built artifact directory containing paired bit/HWH/manifest')
    p=sub.add_parser('board-check');p.add_argument('--boot',action='store_true')
    p=sub.add_parser('smoke');p.add_argument('--runs',type=int,default=2)
    p=sub.add_parser('rtl');p.add_argument('action',choices=['sim','implement'])
    p.add_argument('--tests',nargs='+',default=['network']);p.add_argument('--vivado-bin')
    p=sub.add_parser('overlay',help='Full AXI/PS7 overlay sources, protocol simulation and bit/HWH build')
    p.add_argument('action',choices=['check','sim','build','finish'])
    p.add_argument('--tests',nargs='+',default=['bridge','network']);p.add_argument('--vivado-bin')
    p.add_argument('--workspace',help='Existing routed workspace, used only by finish')
    p=sub.add_parser('export');p.add_argument('--output',required=True)
    args=parser.parse_args()
    if args.command=='setup':setup(args)
    elif args.command=='configure':configure(args)
    elif args.command=='platform-check':platform_check(args)
    elif args.command in ('trust','deploy','board-check'):dependency_command(args)
    elif args.command=='doctor':
        runtime(['build/doctor.py',*(['--gui'] if args.gui else [])])
        if args.gui:runtime(['sim/gui_smoke.py'])
    elif args.command=='verify':run([sys.executable,'-B','sim/verify_package.py'])
    elif args.command=='test':
        run([sys.executable,'-B','sim/verify_package.py'])
        runtime(['sim/run_offline.py'])
    elif args.command=='start':
        runtime(['build/doctor.py','--gui'])
        runtime(['src/desktop/desktop.py'])
    elif args.command=='smoke':runtime(['src/stream/tools/run_measurement.py','--limit','3',
                                       '--runs',str(args.runs),'--golden','30','--full-wire-report'])
    elif args.command=='rtl':rtl(args)
    elif args.command=='overlay':overlay(args)
    elif args.command=='export':export(args)


if __name__=='__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        print('ERROR:',str(exc),file=sys.stderr)
        raise SystemExit(1)
