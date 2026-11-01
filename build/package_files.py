"""Define a portable distribution independent of machine-local runtime output."""
from pathlib import Path
import os

EXCLUDED_PARTS = {'.runtime', '.venv', '__pycache__', '.git', 'xsim.dir', '.xil'}
EXCLUDED_FILES = {'board/local_config.json', 'board/known_hosts'}


def package_files(root):
    root=Path(root)
    for folder,dirs,files in os.walk(root):
        relative=Path(folder).relative_to(root).as_posix()
        dirs[:]=sorted(d for d in dirs if d.lower() not in EXCLUDED_PARTS and
                       (relative+'/'+d).lstrip('./') not in
                       ('src/core/build','src/stream/results','src/stream/reports',
                        'src/desktop/records','sim/results'))
        for name in sorted(files):
            path=Path(folder)/name
            if path.relative_to(root).as_posix() in EXCLUDED_FILES:continue
            if path.suffix.lower() in ('.pyc','.so','.wdb','.jou','.pb','.log','.wcfg'):continue
            yield path
