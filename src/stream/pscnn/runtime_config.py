"""One portable configuration for desktop, measurements and deployment."""
import json
import os
import shlex
import tempfile
from pathlib import Path, PurePosixPath

PACKAGE = Path(__file__).resolve().parents[3]
CONFIG = PACKAGE / 'board/local_config.json'
DEFAULTS = dict(host='192.168.2.99', username='xilinx', port=22,
                remote_base='/home/xilinx/pynq_cnn_delivery',
                remote_root='/home/xilinx/pynq_cnn_delivery',
                remote_python='/usr/local/share/pynq-venv/bin/python3')


def load_config():
    values = dict(DEFAULTS)
    if CONFIG.exists():
        values.update(json.loads(CONFIG.read_text(encoding='utf-8')))
    values['known_hosts'] = str(PACKAGE / 'board/known_hosts')
    return values


def save_config(values, path=None):
    """Publish a complete configuration, retaining the old one on write failure."""
    path=Path(path) if path else CONFIG
    descriptor,name=tempfile.mkstemp(prefix='config_',suffix='.tmp',dir=str(path.parent))
    temporary=Path(name)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8',newline='\n') as stream:
            json.dump(values,stream,indent=2)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if temporary.exists():temporary.unlink()


def stream_root(config=None):
    return str(PurePosixPath((config or load_config())['remote_root']) / 'src/stream')


def service_command(output_dir, golden=0, workers=2, preprocess='native',
                    ack_words=1, full_wire_report=False, config=None):
    config = config or load_config()
    root = PurePosixPath(config['remote_root'])
    args = [config['remote_python'], '-B', str(root/'src/stream/tools/serve_images.py'),
            '--out-dir', output_dir, '--legacy-root', str(root/'board/base'),
            '--golden', str(golden), '--workers', str(workers), '--preprocess', preprocess,
            '--ack-words', str(ack_words)]
    if full_wire_report:
        args.append('--full-wire-report')
    return 'sudo -S -p "" ' + ' '.join(shlex.quote(arg) for arg in args)
