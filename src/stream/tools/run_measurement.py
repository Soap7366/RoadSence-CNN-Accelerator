"""Include PC interpreter/module startup in the measurement origin."""
import time
LAUNCH_NS = time.perf_counter_ns()
import os
import subprocess
import sys
from pathlib import Path

if __name__ == '__main__':
    env = dict(os.environ, PSCNN_LAUNCH_NS=str(LAUNCH_NS))
    command = [sys.executable, '-B', str(Path(__file__).with_name('client_images.py')), *sys.argv[1:]]
    raise SystemExit(subprocess.call(command, env=env))
