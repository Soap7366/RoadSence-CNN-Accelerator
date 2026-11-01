"""Pinned-host SSH. Credentials stay in memory; interactive by default."""
import os
import getpass
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import paramiko
from pscnn.runtime_config import load_config


def connect(host=None, handoff=False, timings=None, password=None):
    config = load_config()
    host = host or config['host']
    timings = timings if timings is not None else {}
    mark = time.perf_counter_ns()
    if handoff:
        raise ValueError('Use interactive credentials; embedded password extraction is disabled')
    if password is None:
        password = getpass.getpass('Board SSH/sudo password: ')
    timings['credentials_read_ns'] = time.perf_counter_ns() - mark
    started = time.perf_counter_ns()
    client = paramiko.SSHClient()
    known = Path(config['known_hosts'])
    if not known.exists():
        raise RuntimeError('No trusted host key. Run: python manage.py trust --fingerprint SHA256:...')
    client.load_host_keys(str(known))
    timings['ssh_client_hostkeys_ns'] = time.perf_counter_ns() - started
    mark = time.perf_counter_ns()
    sock = socket.create_connection((host, config['port']), 8)
    sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
    timings['tcp_connect_ns'] = time.perf_counter_ns() - mark
    class TimedTransport(paramiko.Transport):
        def start_client(self, *args, **kwargs):
            mark = time.perf_counter_ns()
            try:
                return super().start_client(*args, **kwargs)
            finally:
                timings['ssh_handshake_ns'] = time.perf_counter_ns() - mark

        def auth_password(self, *args, **kwargs):
            mark = time.perf_counter_ns()
            try:
                return super().auth_password(*args, **kwargs)
            finally:
                timings['ssh_password_auth_ns'] = time.perf_counter_ns() - mark
    mark = time.perf_counter_ns()
    client.connect(host, port=config['port'], username=config['username'], password=password, sock=sock, look_for_keys=False,
                   allow_agent=False, timeout=8, banner_timeout=10, auth_timeout=10,
                   transport_factory=TimedTransport)
    timings['ssh_connect_call_ns'] = time.perf_counter_ns() - mark
    return client, password, time.perf_counter_ns() - started
