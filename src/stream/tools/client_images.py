"""PC sends 64x64 uint8 gray; PS retains exact normalization and quantization."""
import time
PROCESS_START_NS = time.perf_counter_ns()
IMPORT_TIMINGS = {}
_mark = time.perf_counter_ns()
import argparse
import hashlib
import json
import queue
import sys
import threading
import time
import uuid
import shlex
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
IMPORT_TIMINGS['standard_library_ns'] = time.perf_counter_ns() - _mark
_mark = time.perf_counter_ns()
from pscnn.image_stream import request, MAX_IMAGE_BYTES
IMPORT_TIMINGS['protocol_modules_ns'] = time.perf_counter_ns() - _mark
_mark = time.perf_counter_ns()
from tools.board_connection import connect
from pscnn.runtime_config import load_config, stream_root, service_command
IMPORT_TIMINGS['paramiko_connection_modules_ns'] = time.perf_counter_ns() - _mark
IMPORT_FINISHED_NS = time.perf_counter_ns()


def network_snapshot(client):
    code = """import json,pathlib,time
p=pathlib.Path('/sys/class/net/eth0')
v={'monotonic_ns':time.perf_counter_ns()}
for k in ['rx_bytes','rx_packets','rx_errors','rx_crc_errors','rx_frame_errors','rx_length_errors','rx_dropped','tx_bytes','tx_packets','tx_errors','tx_dropped']:
 try:v[k]=int((p/'statistics'/k).read_text())
 except Exception:v[k]=None
for k in ['speed','duplex','carrier']:
 try:v[k]=(p/k).read_text().strip()
 except Exception:v[k]=None
lines=pathlib.Path('/proc/net/snmp').read_text().splitlines()
for i,line in enumerate(lines):
 if line.startswith('Tcp:') and 'RetransSegs' in line:
  v['tcp']=dict(zip(line.split()[1:],map(int,lines[i+1].split()[1:])))
print(json.dumps(v))
"""
    _, stdout, stderr = client.exec_command('/usr/local/share/pynq-venv/bin/python3 -c ' + shlex.quote(code))
    result = json.loads(stdout.read())
    if stdout.channel.recv_exit_status():
        raise RuntimeError('Network snapshot failed: ' + stderr.read().decode())
    return result


class Receiver:
    def __init__(self, channel, origin):
        self.channel = channel; self.origin = origin
        self.messages = queue.Queue(); self.receipts = []; self.stderr = bytearray(); self.error = None
        self.thread = threading.Thread(target=self.receive, daemon=True); self.thread.start()

    def receive(self):
        buffer = bytearray()
        try:
            while True:
                recv_start = time.perf_counter_ns()
                block = self.channel.recv(65536)
                recv_end = time.perf_counter_ns()
                buffer.extend(block)
                while b'\n' in buffer:
                    line, _, rest = buffer.partition(b'\n'); buffer = bytearray(rest)
                    at = time.perf_counter_ns()
                    event = json.loads(line)
                    parsed = time.perf_counter_ns()
                    receipt = {'pc_received_ns': at - self.origin, 'event': event,
                               'recv_call_ns': recv_end - recv_start,
                               'recv_includes_blocking_wait':True,
                               'recv_block_bytes': len(block), 'line_bytes': len(line) + 1,
                               'json_parse_ns': parsed - at}
                    self.receipts.append(receipt); self.messages.put(receipt)
                if self.channel.recv_stderr_ready():
                    self.stderr.extend(self.channel.recv_stderr(65536))
                if not block:
                    return
        except BaseException as exc:
            self.error = exc

    def get(self, expected=None, timeout=60):
        try:
            receipt = self.messages.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError('No board response: ' + str(self.error) + ' ' + self.stderr.decode(errors='replace'))
        event = receipt['event']
        if event.get('event') == 'service_complete' and event.get('status') != 'PASS':
            raise RuntimeError('Board failure: ' + str(event))
        if expected and event.get('event') != expected:
            raise RuntimeError('Unexpected event: ' + str(event))
        return receipt


def main():
    main_start = time.perf_counter_ns()
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default=load_config()['host'])
    parser.add_argument('--input-dir', default=str(ROOT.parents[1] / 'data/images'))
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--window', type=int, choices=(1, 2), default=2)
    parser.add_argument('--golden', type=int, choices=(0, 30), default=0)
    parser.add_argument('--ack-words', type=int, choices=(1, 16, 64, 1024), default=1)
    parser.add_argument('--handoff-credentials', action='store_true')
    parser.add_argument('--workers',type=int,choices=(1,2),default=2)
    parser.add_argument('--preprocess',choices=('reference','native'),default='native')
    parser.add_argument('--full-wire-report',action='store_true')
    args = parser.parse_args()
    paths = sorted(Path(args.input_dir).glob('*.jpg'))[:args.limit]
    if not paths or args.runs < 1:
        raise ValueError('Images and positive run count required')
    job = 'job_' + uuid.uuid4().hex[:12]; out = ROOT / 'results' / job; out.mkdir(parents=True)
    origin = int(os.environ.get('PSCNN_LAUNCH_NS', PROCESS_START_NS))
    report = {'job': job, 'status': 'FAIL', 'images': [], 'runs': [], 'golden': args.golden,
              'ack_words': args.ack_words, 'window': args.window, 'includes_pc_preprocessing': False,
              'version': '0.3.0', 'wire_format': 'IMG1: 44-byte header + original JPEG',
              'preprocess':args.preprocess,'workers':args.workers,
              'full_wire_report':args.full_wire_report,
              'origin_ns': origin, 'process_started_ns': PROCESS_START_NS,
              'startup': dict(IMPORT_TIMINGS), 'connection_timings': {}, 'timeline': []}
    report['startup']['launcher_to_child_entry_ns'] = PROCESS_START_NS - origin
    report['startup']['entry_gap_ns'] = main_start - IMPORT_FINISHED_NS
    report['startup']['arguments_job_setup_ns'] = time.perf_counter_ns() - main_start
    def timed(name, operation):
        begin = time.perf_counter_ns(); result = operation(); end = time.perf_counter_ns()
        report['timeline'].append(dict(name=name, start_ns=begin-origin, end_ns=end-origin, duration_ns=end-begin))
        return result
    client = None
    try:
        client, password, connection_ns = timed('connection_with_credentials', lambda: connect(args.host, args.handoff_credentials, report['connection_timings']))
        report['connection_ns'] = connection_ns
        remote_out = stream_root() + '/reports/' + job; report['remote_output'] = remote_out
        command = service_command(remote_out, golden=args.golden, workers=args.workers,
                                  preprocess=args.preprocess, ack_words=args.ack_words,
                                  full_wire_report=args.full_wire_report)
        mark = time.perf_counter_ns()
        stdin, stdout, stderr = client.exec_command(command)
        report['exec_channel_ns'] = time.perf_counter_ns() - mark
        channel = stdout.channel
        # Wait for ready before sending any binary; stdin is kept open throughout the session.
        channel.sendall((password + '\n').encode()); password = None
        receiver = Receiver(channel, origin)
        ready = receiver.get('ready')
        report['command_to_ready_ns'] = time.perf_counter_ns() - mark
        report['timeline'].append(dict(name='command_to_ready', start_ns=mark-origin,
                                      end_ns=time.perf_counter_ns()-origin, duration_ns=report['command_to_ready_ns']))
        report['ready'] = ready
        for run_id in range(args.runs):
            run = {'run_id': run_id, 'pc_start_ns': time.perf_counter_ns() - origin, 'sent': [], 'results': []}
            channel.sendall(request(b'BEG1', run_id, len(paths)))
            receiver.get('run_start')
            run['begin_ack_ns'] = time.perf_counter_ns() - origin
            capacity = threading.Semaphore(args.window); sender_errors = []
            def send():
                try:
                    for index, path in enumerate(paths):
                        wait_start = time.perf_counter_ns()
                        if not capacity.acquire(timeout=60):
                            raise TimeoutError('Sender waiting for result capacity')
                        started = time.perf_counter_ns()
                        data = path.read_bytes()
                        read_end = time.perf_counter_ns()
                        if not 0 < len(data) <= MAX_IMAGE_BYTES:
                            raise ValueError('Invalid file size')
                        digest = hashlib.sha256(data).digest()
                        source_digest=digest.hex()
                        hash_end = time.perf_counter_ns()
                        pc_timing={}
                        header = request(b'IMG1', index, len(data), digest)
                        header_end = time.perf_counter_ns()
                        wire = header + data
                        mark = time.perf_counter_ns()
                        channel.sendall(wire)
                        send_end = time.perf_counter_ns()
                        pc_timing.update(capacity_wait_ns=started-wait_start, read_ns=read_end-started,
                                         source_sha_ns=hash_end-read_end,
                                         header_pack_ns=header_end-hash_end, wire_concat_ns=mark-header_end,
                                         sendall_ns=send_end-mark)
                        run['sent'].append({'frame_id': index, 'file': path.name, 'bytes': len(data),
                                            'source_bytes': len(data), 'source_sha256': source_digest,
                                            'sha256': digest.hex(), 'timing': pc_timing,
                                            'pc_started_ns': started - origin, 'send_started_ns': mark - origin,
                                            'send_finished_ns': send_end - origin})
                    channel.sendall(request(b'END1', run_id))
                except BaseException as exc:
                    sender_errors.append(str(exc)); channel.close()
            sender = threading.Thread(target=send, daemon=True); sender.start()
            for index in range(len(paths)):
                result = receiver.get('image_result')
                validate_start = time.perf_counter_ns()
                event = result['event']
                if event['run_id'] != run_id or event['frame_id'] != index:
                    raise RuntimeError('Response identity mismatch')
                if not event['native']['readback_verified']:
                    raise RuntimeError('Missing complete input readback')
                result['pc_validate_ns'] = time.perf_counter_ns() - validate_start
                result['pc_validated_ns'] = time.perf_counter_ns() - origin
                run['results'].append(result); capacity.release()
            receiver.get('run_complete'); sender.join(timeout=5)
            if sender.is_alive() or sender_errors or len(run['sent']) != len(paths):
                raise RuntimeError('Sender failed: ' + str(sender_errors))
            for sent, received in zip(run['sent'], run['results']):
                if sent['sha256'] != received['event']['source_sha256']:
                    raise RuntimeError('Original JPEG identity mismatch')
            run['pc_end_ns'] = time.perf_counter_ns() - origin
            run['elapsed_ns'] = run['pc_end_ns'] - run['pc_start_ns']
            report['runs'].append(run)
            print('run', run_id, 'images', len(paths), 'seconds', round(run['elapsed_ns']/1e9, 4), flush=True)
        close_start = time.perf_counter_ns()
        channel.sendall(request(b'BYE1')); channel.shutdown_write()
        completed=receiver.get('service_complete')
        receiver.thread.join(timeout=20)
        report['network_before']=completed['event']['network_before']
        report['network_after']=completed['event']['network_after']
        if receiver.thread.is_alive():
            raise RuntimeError('Service did not exit')
        report['remote_exit_code'] = channel.recv_exit_status()
        report['stderr'] = receiver.stderr.decode(errors='replace')
        if report['remote_exit_code'] != 0:
            raise RuntimeError('Remote failed')
        report['timeline'].append(dict(name='service_finish_and_wait_exit', start_ns=close_start-origin,
                                      end_ns=time.perf_counter_ns()-origin, duration_ns=time.perf_counter_ns()-close_start))
        report['receipts'] = receiver.receipts
        report['pc_all_results_ns'] = report['runs'][-1]['results'][-1]['pc_received_ns']
        def download():
            with client.open_sftp() as sftp:
                sftp.get(remote_out + '/board_summary.json', str(out / 'board_summary.json'))
        timed('download_board_report', download)
        report['status'] = 'PASS'
    except BaseException as exc:
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    finally:
        if client is not None:
            timed('ssh_close', client.close)
        report['total_ns'] = time.perf_counter_ns() - origin
        (out / 'pc_summary.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    print('RESULT', out, report['status'], report.get('error'), flush=True)
    return 0 if report['status'] == 'PASS' else 2


if __name__ == '__main__':
    sys.exit(main())
