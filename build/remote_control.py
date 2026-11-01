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
import time
import tarfile
import threading
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


class RemoteCommandError(RuntimeError):
    pass


def execute(client,command,password=None,timeout=180):
    marker='PSCNN_DONE_'+uuid.uuid4().hex
    receipt='/tmp/'+marker.lower()+'.json'
    transcript='/tmp/'+marker.lower()+'.out'
    # Publish a durable command receipt as well as the channel marker. It lets
    # the PC recover an already-completed command without repeating side effects.
    wrapped=('( '+command+' ) > '+shlex.quote(transcript)+' 2>&1; pscnn_status=$?; '
             'printf "%s" "$pscnn_status" > '+shlex.quote(receipt)+'.tmp; mv '+shlex.quote(receipt)+'.tmp '+shlex.quote(receipt)+'; '
             'cat '+shlex.quote(transcript)+'; printf "\\n'+marker+':%s\\n" "$pscnn_status"; exit "$pscnn_status"')
    stdin,stdout,stderr=client.exec_command(wrapped,timeout=timeout)
    if password is not None:
        stdin.write(password+'\n');stdin.flush()
    # These are finite deployment commands. Signal EOF after the optional sudo
    # password so sshd can finish the channel after the remote command exits.
    stdin.channel.shutdown_write()
    channel=stdout.channel
    output=[]
    deadline=time.monotonic()+timeout
    import codecs
    decoder=codecs.getincrementaldecoder('utf-8')(errors='replace')
    error_decoder=codecs.getincrementaldecoder('utf-8')(errors='replace')
    started=last_report=time.monotonic()
    completion=None
    display_buffer=''
    while True:
        while channel.recv_ready():
            chunk=decoder.decode(channel.recv(65536))
            output.append(chunk)
            display_buffer+=chunk
            while '\n' in display_buffer:
                line,display_buffer=display_buffer.split('\n',1)
                if not line.startswith(marker+':'):print(line,flush=True)
        while channel.recv_stderr_ready():
            print(error_decoder.decode(channel.recv_stderr(65536)),end='',file=sys.stderr,flush=True)
        match=re.search(r'\n'+marker+r':(\d+)\r?\n',''.join(output))
        if match:
            completion=int(match.group(1))
            break
        if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
            break
        if time.monotonic()>deadline:
            channel.close()
            raise TimeoutError(f'Remote command exceeded {timeout} seconds: '+command)
        if time.monotonic()-last_report>=5:
            print(f'等待板端命令完成 · {int(time.monotonic()-started)} 秒（上限 {timeout} 秒）',flush=True)
            last_report=time.monotonic()
            # The SSH exit message can be delayed even after remote processes
            # exit. An independent channel can read the durable result instead.
            probe=None
            try:
                probe=client.get_transport().open_session(timeout=3)
                probe.settimeout(3)
                probe.exec_command('if [ -f '+shlex.quote(receipt)+' ]; then cat '+shlex.quote(receipt)+'; printf "\\n"; cat '+shlex.quote(transcript)+'; fi')
                data=bytearray()
                probe_deadline=time.monotonic()+3
                while time.monotonic()<probe_deadline:
                    if probe.recv_ready():data.extend(probe.recv(65536))
                    elif probe.exit_status_ready():break
                    else:time.sleep(.02)
                first,separator,body=bytes(data).partition(b'\n')
                if separator and first.isdigit():
                    completion=int(first)
                    output=[body.decode('utf-8',errors='replace')]
                    display_buffer=''
                    print(output[0],end='',flush=True)
                    break
            except (OSError,paramiko.SSHException):
                pass
            finally:
                if probe is not None:probe.close()
        time.sleep(.02)
    tail=decoder.decode(b'',final=True)
    output.append(tail)
    display_buffer+=tail
    if display_buffer and not display_buffer.startswith(marker+':'):
        print(display_buffer,end='',flush=True)
    print(error_decoder.decode(b'',final=True),end='',file=sys.stderr,flush=True)
    code=completion if completion is not None else channel.recv_exit_status()
    channel.close()
    if code:raise RemoteCommandError('Remote command failed (%s): %s'%(code,command))
    return re.sub(r'\n'+marker+r':\d+\r?\n','', ''.join(output))


def remote_python(cfg,path,*args,sudo=False):
    command=' '.join(shlex.quote(str(x)) for x in [cfg['remote_python'],'-u','-B',path,*args])
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


def upload_bundle(client, source, release):
    """One compressed upload plus exact archive readback; validate extracted files next."""
    runtime=ROOT/'.runtime';runtime.mkdir(exist_ok=True)
    archive=runtime/('upload_'+uuid.uuid4().hex[:8]+'.tar.gz')
    files=list(package_files(source))
    print(f'正在打包 {len(files)} 个交付文件……',flush=True)
    with tarfile.open(archive,'w:gz',compresslevel=3) as bundle:
        def metadata(info):
            info.mtime=0
            return info
        for path in files:
            bundle.add(path,arcname=path.relative_to(source).as_posix(),recursive=False,filter=metadata)
    size=archive.stat().st_size
    remote_name=release+'.tar.gz'
    activity=[time.monotonic()]
    finished=threading.Event()
    expired=threading.Event()
    def watchdog():
        while not finished.wait(.2):
            if time.monotonic()-activity[0]>30:
                expired.set()
                # Paramiko's packet write loop retries socket timeouts. Interrupt
                # the underlying TCP socket so even that loop must unwind.
                sock=client.get_transport().sock
                try:sock.shutdown(socket.SHUT_RDWR)
                except OSError:pass
                sock.close()
                return
    monitor=threading.Thread(target=watchdog,daemon=True);monitor.start()
    last_report=[0.0]
    def progress(done,total):
        activity[0]=time.monotonic()
        if activity[0]-last_report[0]>=1 or done==total:
            print(f'上传 {done/1e6:.1f}/{total/1e6:.1f} MB ({done/total:.0%})',flush=True)
            last_report[0]=activity[0]
    try:
        with client.open_sftp() as sftp:
            sftp.get_channel().settimeout(20)
            print(f'开始上传压缩包：{size/1e6:.1f} MB',flush=True)
            sftp.put(str(archive),remote_name,callback=progress)
            expected=hashlib.sha256(archive.read_bytes()).digest()
            print('正在读回压缩包校验……',flush=True)
            observed=hashlib.sha256()
            with sftp.open(remote_name,'rb') as remote:
                while True:
                    block=remote.read(65536)
                    if not block:break
                    observed.update(block);activity[0]=time.monotonic()
            if observed.digest()!=expected:raise RuntimeError('Upload archive readback differs')
        finished.set();monitor.join(timeout=1)
        print('压缩包 SHA256 读回一致；正在解压并校验全部文件……',flush=True)
        execute(client,'mkdir '+shlex.quote(release)+' && tar -xzf '+shlex.quote(remote_name)+' -C '+shlex.quote(release),timeout=60)
    except Exception as exc:
        if expired.is_set():raise TimeoutError('板端文件上传/读回连续30秒无响应，请检查网线和板卡。') from exc
        raise
    finally:
        finished.set();monitor.join(timeout=1)
        archive.unlink(missing_ok=True)


def deploy(args, password=None):
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
    client,secret,_=connect(password=password)
    release=base+'/releases/release_'+time.strftime('%Y%m%d_%H%M%S',time.gmtime())+'_'+uuid.uuid4().hex[:8]
    q=shlex.quote
    try:
        # Existing non-managed directories are never adopted automatically.
        marker=base+'/.package_owner'
        command=('if [ -e '+q(base)+' ]; then test "$(cat '+q(marker)+
                 ' 2>/dev/null)" = pynq_cnn_delivery; else mkdir '+q(base)+
                 ' && printf %s pynq_cnn_delivery > '+q(marker)+'; fi')
        execute(client,command)
        execute(client,'mkdir -p '+q(base+'/releases'))
        upload_bundle(client,source,release)
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


def board_check(args, password=None):
    cfg=load_config();client,secret,_=connect(password=password)
    try:
        options=['--boot'] if args.boot else []
        output=execute(client,remote_python(cfg,cfg['remote_root']+'/board/setup.py',*options,sudo=args.boot),
                       password=secret if args.boot else None,timeout=100 if args.boot else 45)
        path=ROOT/'sim/results';path.mkdir(exist_ok=True)
        (path/'board_check.log').write_text(output,encoding='utf-8')
    finally:
        secret=None;client.close()
