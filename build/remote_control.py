"""SSH deployment into a new release, without overwriting earlier projects."""
import base64
import getpass
import hashlib
import json
import re
import shutil
import shlex
import socket
import sys
import threading
import time
import uuid
from pathlib import Path, PurePosixPath

import paramiko

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/stream'))
from pscnn.runtime_config import CONFIG, load_config, save_config
from tools.board_connection import connect
from build.package_files import package_files


def fingerprint(key):
    return 'SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')


def trust(args):
    cfg=load_config()
    with socket.create_connection((cfg['host'],cfg['port']),timeout=10) as sock:
        with paramiko.Transport(sock) as transport:
            transport.start_client(timeout=10)
            key=transport.get_remote_server_key()
            actual=fingerprint(key)
            if actual!=args.fingerprint:
                raise ValueError('Host fingerprint mismatch; observed '+actual)
            path=Path(cfg['known_hosts'])
            keys=paramiko.HostKeys()
            if path.exists():keys.load(str(path))
            host=cfg['host'] if cfg['port']==22 else '[%s]:%s'%(cfg['host'],cfg['port'])
            keys.add(host,key.get_name(),key);keys.save(str(path))
            print('Trusted verified host:',cfg['host'],actual)


def execute(client,command,password=None):
    stdin,stdout,stderr=client.exec_command(command,timeout=180)
    if password is not None:
        stdin.write(password+'\n');stdin.flush()
    errors=[]
    read_errors=[]
    def drain():
        try:
            for line in iter(stderr.readline,''):
                errors.append(line);print(line,end='',file=sys.stderr,flush=True)
        except BaseException as exc:
            read_errors.append(exc)
    worker=threading.Thread(target=drain,daemon=True);worker.start()
    output=[]
    for line in iter(stdout.readline,''):
        output.append(line);print(line,end='',flush=True)
    code=stdout.channel.recv_exit_status();worker.join(timeout=10)
    if worker.is_alive() or read_errors:
        raise RuntimeError('Remote stderr was not fully drained: '+str(read_errors))
    if code:raise RuntimeError('Remote command failed (%s): %s'%(code,command))
    return ''.join(output)


def remote_python(cfg,path,*args,sudo=False):
    command=' '.join(shlex.quote(str(x)) for x in [cfg['remote_python'],'-B',path,*args])
    return 'sudo -S -p "" '+command if sudo else command


def release_base(config):
    base=config['remote_base']
    if not re.fullmatch(r'/home/[a-z_][a-z0-9_-]*/[a-z0-9_-]+',base):
        raise ValueError('Unsafe remote_base')
    if PurePosixPath(base).name.startswith(('systolic','obstacle')):
        raise ValueError('Use a new deployment directory, not an existing project')
    return base


def mkdir(sftp,path):
    try:sftp.stat(path)
    except FileNotFoundError:sftp.mkdir(path)


def deploy(args):
    cfg=load_config();base=release_base(cfg)
    source=ROOT
    overlay=getattr(args,'overlay',None)
    if overlay:
        from pscnn.model import ModelSpec
        from pscnn.overlay_artifact import validate_overlay
        candidate=Path(overlay).resolve()
        verified=validate_overlay(candidate/'overlay_manifest.json',ModelSpec.load())
        source=ROOT/'.runtime'/('deploy_'+uuid.uuid4().hex[:8]);source.mkdir(parents=True)
        for path in package_files(ROOT):
            target=source/path.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(path,target)
        target=source/'board/base/overlays/cnn_v1'
        for name in ('cnn.bit','cnn.hwh','overlay_manifest.json'):
            shutil.copyfile(candidate/name,target/name)
        inventory=source/'report/source_inventory.json'
        entries=json.loads(inventory.read_text(encoding='utf-8'))
        for entry in entries['files']:
            path=source/entry['destination']
            entry['packaged_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            entry['adapted']=entry['packaged_sha256']!=entry['source_sha256']
        inventory.write_text(json.dumps(entries,ensure_ascii=False,indent=2),encoding='utf-8')
        (source/'report/overlay_override.json').write_text(json.dumps(dict(
            source='explicit deploy --overlay argument',bit_sha256=verified['bit_sha256'],
            hwh_sha256=verified['hwh_sha256'],board_verified=False),indent=2),encoding='utf-8')
        manifest={p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in package_files(source) if p.relative_to(source).as_posix()!='report/package_manifest.json'}
        (source/'report/package_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    client,secret,_=connect()
    release=base+'/releases/release_'+time.strftime('%Y%m%d_%H%M%S',time.gmtime())+'_'+uuid.uuid4().hex[:8]
    q=shlex.quote
    try:
        # Existing non-managed directories are never adopted automatically.
        marker=base+'/.package_owner'
        command=('if [ -e '+q(base)+' ]; then test "$(cat '+q(marker)+
                 ' 2>/dev/null)" = pynq_cnn_delivery; else mkdir '+q(base)+
                 ' && printf %s pynq_cnn_delivery > '+q(marker)+'; fi')
        execute(client,command)
        with client.open_sftp() as sftp:
            mkdir(sftp,base+'/releases');sftp.mkdir(release)
            made={release}
            for path in package_files(source):
                name=path.relative_to(source).as_posix();parent=PurePosixPath(release+'/'+name).parent
                for item in reversed([parent,*parent.parents]):
                    value=str(item)
                    if value==release or value.startswith(release+'/'):
                        if value not in made:mkdir(sftp,value);made.add(value)
                sftp.put(str(path),release+'/'+name)
                with sftp.open(release+'/'+name,'rb') as remote:
                    if hashlib.sha256(remote.read()).digest()!=hashlib.sha256(path.read_bytes()).digest():
                        raise RuntimeError('Upload readback differs: '+name)
        execute(client,remote_python(cfg,release+'/sim/verify_package.py'))
        execute(client,remote_python(cfg,release+'/board/setup.py','--build'))
        # Activate locally only after remote source, environment, native build and tests pass.
        cfg.pop('known_hosts',None);cfg['remote_root']=release
        save_config(cfg,path=CONFIG)
        record=dict(status='PASS',remote_root=release,host=cfg['host'],
                    hardware_booted=False,scope='upload readback, offline identity, board native build and mock tests')
        folder=ROOT/'sim/results';folder.mkdir(exist_ok=True)
        (folder/'deployment.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
        print('Deployed. Next: python manage.py board-check --boot')
    finally:
        secret=None;client.close()


def board_check(args):
    cfg=load_config();client,secret,_=connect()
    try:
        options=['--boot'] if args.boot else []
        output=execute(client,remote_python(cfg,cfg['remote_root']+'/board/setup.py',*options,sudo=args.boot),
                       password=secret if args.boot else None)
        path=ROOT/'sim/results';path.mkdir(exist_ok=True)
        (path/'board_check.log').write_text(output,encoding='utf-8')
    finally:
        secret=None;client.close()
