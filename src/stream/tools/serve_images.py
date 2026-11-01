"""Persistent PS pipeline: uint8 gray receive -> normalize/quantize -> PL -> JSONL."""
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
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
IMPORT_TIMINGS['standard_library_ns'] = time.perf_counter_ns() - _mark
_mark = time.perf_counter_ns()
from pscnn.model import ModelSpec
IMPORT_TIMINGS['model_module_ns'] = time.perf_counter_ns() - _mark
_mark = time.perf_counter_ns()
from pscnn.overlay_artifact import validate_overlay
IMPORT_TIMINGS['artifact_module_ns'] = time.perf_counter_ns() - _mark
_mark = time.perf_counter_ns()
from pscnn.boot_state import load_boot_state
from pscnn.native_session import NativeSession
from pscnn.pinned_mmio import PinnedMMIO
IMPORT_TIMINGS['hardware_modules_ns'] = time.perf_counter_ns() - _mark
_mark = time.perf_counter_ns()
from pscnn.postprocess import decode_outputs
from pscnn.image_stream import read_message, prepare_bytes
from pscnn.fast_preprocess import FastPreprocessor
IMPORT_TIMINGS['prepost_modules_ns'] = time.perf_counter_ns() - _mark
IMPORT_FINISHED_NS = time.perf_counter_ns()


def emit(value, details=None):
    mark = time.perf_counter_ns()
    line = json.dumps(value, separators=(',', ':'), allow_nan=False) + '\n'
    encoded = time.perf_counter_ns()
    sys.stdout.write(line)
    written = time.perf_counter_ns()
    sys.stdout.flush()
    finished = time.perf_counter_ns()
    if details is not None:
        details.update(json_encode_ns=encoded-mark, stdout_write_ns=written-encoded,
                       stdout_flush_ns=finished-written, response_bytes=len(line.encode('utf-8')))


def network_snapshot():
    root=Path('/sys/class/net/eth0');result={'monotonic_ns':time.perf_counter_ns()}
    for key in ('rx_bytes','rx_packets','rx_errors','rx_crc_errors','rx_frame_errors','rx_length_errors','rx_dropped','tx_bytes','tx_packets','tx_errors','tx_dropped'):
        try:result[key]=int((root/'statistics'/key).read_text())
        except OSError:result[key]=None
    for key in ('speed','duplex','carrier'):
        try:result[key]=(root/key).read_text().strip()
        except OSError:result[key]=None
    lines=Path('/proc/net/snmp').read_text().splitlines()
    for i,line in enumerate(lines):
        if line.startswith('Tcp:') and 'RetransSegs' in line:
            result['tcp']=dict(zip(line.split()[1:],map(int,lines[i+1].split()[1:])))
    return result


def pipeline(model, raw, prepared, failed,processor=None,workers=1):
    from concurrent.futures import ThreadPoolExecutor
    pool=ThreadPoolExecutor(max_workers=workers)
    def prepare_one(message,details):
        kind,index,size,data,started,received=message
        prep_start=time.perf_counter_ns()
        payload,timing=processor.prepare(data) if processor else prepare_bytes(data,model)
        timing.update(details)
        source_sha=timing.pop('source_sha256')
        timing.update(receive_started_ns=started,received_ns=received,
                      prep_started_ns=prep_start,prep_finished_ns=time.perf_counter_ns())
        return kind,index,size,payload,timing,source_sha
    def receive():
        try:
            active = False
            expected = count = run = 0
            while True:
                details = {}
                message = read_message(sys.stdin.buffer, details)
                kind, index, size, data, _, _ = message
                if kind == b'BEG1':
                    if active or not 1 <= size <= 100000:
                        raise ValueError('Invalid batch begin')
                    active = True; run = index; count = size; expected = 0
                elif kind == b'IMG1':
                    if not active or index != expected or expected >= count:
                        raise ValueError('Out of order image')
                    expected += 1
                elif kind == b'END1':
                    if not active or index != run or expected != count:
                        raise ValueError('Incomplete batch end')
                    active = False
                elif kind == b'BYE1':
                    if active:
                        raise ValueError('Close during unfinished batch')
                else:
                    raise ValueError('This service accepts original IMG1 JPEG frames only')
                if kind==b'IMG1':
                    future=pool.submit(prepare_one,message,details)
                    prepared.put((kind,index,size,future,None,None))
                else:
                    prepared.put((kind,index,size,None,None,None))
                if kind == b'BYE1':
                    pool.shutdown(wait=True)
                    return
        except BaseException as exc:
            failed.put(exc)

    threading.Thread(target=receive,daemon=True).start()
    return pool


def main():
    import fcntl
    main_started = time.perf_counter_ns()
    parser = argparse.ArgumentParser()
    parser.add_argument('--legacy-root', default=str(ROOT.parents[1] / 'board/base'))
    parser.add_argument('--golden', type=int, choices=(0, 30), default=0)
    parser.add_argument('--ack-words', type=int, choices=(1, 16, 64, 1024), default=1)
    parser.add_argument('--out-dir', required=True)
    parser.add_argument('--workers',type=int,choices=(1,2),default=2)
    parser.add_argument('--preprocess',choices=('reference','native'),default='native')
    parser.add_argument('--full-wire-report',action='store_true')
    args = parser.parse_args()
    out = Path(args.out_dir).resolve()
    if ROOT not in out.parents:
        raise ValueError('Reports must be in new stream project')
    out.mkdir(parents=True, exist_ok=False)
    record = {'status': 'FAIL', 'version': '0.3.0', 'startup': dict(IMPORT_TIMINGS),
              'golden_passed': 0, 'results': [], 'runs': []}
    record['startup']['arguments_output_dir_ns'] = time.perf_counter_ns() - main_started
    record['startup']['entry_gap_ns'] = main_started - IMPORT_FINISHED_NS
    def startup(name, operation):
        mark = time.perf_counter_ns(); value = operation()
        record['startup'][name + '_ns'] = time.perf_counter_ns() - mark
        return value
    try:
        legacy = Path(args.legacy_root)
        model = startup('model_load', ModelSpec.load)
        artifact_detail = {}
        artifact = startup('validate_overlay', lambda: validate_overlay(legacy / 'overlays/cnn_v1/overlay_manifest.json', model, artifact_detail))
        record['artifact_timings'] = artifact_detail
        with ExitStack() as resources:
            mark = time.perf_counter_ns()
            lock = resources.enter_context(open('/tmp/pscnn_axi_device.lock', 'a+'))
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            record['startup']['device_lock_ns'] = time.perf_counter_ns() - mark
            startup('boot_state', lambda: load_boot_state(artifact))
            mmio = startup('map_mmio', lambda: resources.enter_context(PinnedMMIO(artifact, ROOT / 'native/libpscnn_mmio.so')))
            session = startup('native_session', lambda: NativeSession(mmio, model, ROOT / 'native/libpscnn_frame.so', ack_words=args.ack_words))
            # Read hardware identity and status before ready without running golden.
            mark = time.perf_counter_ns()
            for offset, expected in ((0, 0x53434e4e), (4, 0x10000), (44, 4096)):
                if mmio.read(offset) != expected:
                    raise RuntimeError('Hardware ABI mismatch')
            if mmio.read(12) & (1 | 16) or mmio.read(16):
                raise RuntimeError('Hardware busy or in error; not reset')
            record['startup']['hardware_ready_check_ns'] = time.perf_counter_ns() - mark
            if args.golden:
                mark = time.perf_counter_ns()
                manifest = legacy / 'fixtures/hardware_golden_30/manifest.json'
                golden = json.loads(manifest.read_text())
                if golden['model_id'] != model.model_id or golden['quant_params_sha256'] != model.quant_sha256:
                    raise ValueError('Golden model mismatch')
                for sample in golden['samples']:
                    payload = (manifest.parent / sample['input_file']).read_bytes()
                    if hashlib.sha256(payload).hexdigest() != sample['input_sha256']:
                        raise ValueError('Golden hash mismatch')
                    result = session.infer(payload)
                    if result != {key: sample[key] for key in ('collision_int32', 'steering_int32')}:
                        raise RuntimeError('Golden output mismatch')
                    record['golden_passed'] += 1
                if record['golden_passed'] != 30:
                    raise ValueError('Expected 30 golden samples')
                record['startup']['golden_ns'] = time.perf_counter_ns() - mark
            mark = time.perf_counter_ns()
            raw = queue.Queue(maxsize=2); prepared = queue.Queue(maxsize=2); failed = queue.Queue()
            processor=startup('preprocessor_init',lambda:FastPreprocessor(model,ROOT/'native/libpscnn_preprocess.so')) if args.preprocess=='native' else None
            if processor:resources.callback(processor.close)
            if args.preprocess=='reference':
                startup('reference_module_import',lambda:__import__('pscnn.preprocess'))
            record['quant_table_build_ns']=processor.table_build_ns if processor else None
            record['network_before']=startup('network_before',network_snapshot)
            mark=time.perf_counter_ns()
            pool=pipeline(model,raw,prepared,failed,processor,args.workers)
            resources.callback(pool.shutdown,wait=True)
            record['startup']['pipeline_threads_ns'] = time.perf_counter_ns() - mark
            ready = time.perf_counter_ns()
            record.update(process_started_ns=PROCESS_START_NS, ready_ns=ready, ack_words=args.ack_words,
                          full_readback=True, golden_requested=args.golden, model_id=model.model_id,
                          artifact=artifact)
            record['numpy_loaded_at_ready']='numpy' in sys.modules
            record['preprocess']=args.preprocess;record['workers']=args.workers
            emit({'event': 'ready', 'version': '0.3.0', 'ready_ns': ready,
                  'startup_ns': ready - PROCESS_START_NS, 'golden_passed': record['golden_passed'],
                  'full_readback': True, 'ack_words': args.ack_words, 'queue_limit': 2,
                  'preprocess':args.preprocess,'workers':args.workers})
            run = count = done = 0; last_seq = None
            while True:
                if not failed.empty():
                    raise failed.get()
                try:
                    kind, index, size, payload, timing, source_sha = prepared.get(timeout=.1)
                except queue.Empty:
                    continue
                if kind == b'BEG1':
                    run = index; count = size; done = 0
                    record['runs'].append({'run_id': run, 'count': count, 'start_ns': time.perf_counter_ns()})
                    emit({'event': 'run_start', 'run_id': run, 'count': count})
                elif kind == b'IMG1':
                    kind,index,size,payload,timing,source_sha=payload.result(timeout=30)
                    if index != done:
                        raise ValueError('Prepared order mismatch')
                    timing['infer_started_ns'] = time.perf_counter_ns()
                    result = session.infer(payload, timeout_s=2.)
                    timing['infer_finished_ns'] = time.perf_counter_ns()
                    native = dict(session.last_run)
                    seq = native['result_seq']
                    if last_seq is not None and seq != (last_seq + 1) & 0xffffffff:
                        raise RuntimeError('Nonconsecutive result sequence')
                    last_seq = seq
                    mark = time.perf_counter_ns(); decoded = decode_outputs(result['collision_int32'], result['steering_int32'], model)
                    timing['decode_ns'] = time.perf_counter_ns() - mark
                    row = {'event': 'image_result', 'run_id': run, 'frame_id': index,
                           'source_sha256': source_sha, 'input_sha256': native['input_int8_sha256'],
                           'raw': result, 'decoded': decoded, 'native': native, 'timing': timing}
                    row['timing']['emit_started_ns'] = time.perf_counter_ns()
                    wire=row if args.full_wire_report else {
                        k:row[k] for k in ('event','run_id','frame_id','source_sha256','input_sha256','raw','decoded')}
                    if wire is not row:
                        wire['native']={k:native[k] for k in ('readback_verified','count_checks','bytes_read','bytes_written','result_seq')}
                    emit(wire, timing)
                    row['timing']['emit_finished_ns'] = time.perf_counter_ns()
                    record['results'].append(row); done += 1
                elif kind == b'END1':
                    if index != run or done != count:
                        raise ValueError('Incomplete result batch')
                    record['runs'][-1]['end_ns'] = time.perf_counter_ns()
                    emit({'event': 'run_complete', 'run_id': run, 'count': done})
                elif kind == b'BYE1':
                    break
            record['status'] = 'PASS'
        # Write once after all results; never fsync each image.
    except BaseException as exc:
        record['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    record['network_after']=network_snapshot()
    record['finished_ns'] = time.perf_counter_ns()
    (out / 'board_summary.json').write_text(json.dumps(record, indent=2, allow_nan=False), encoding='utf-8')
    emit({'event': 'service_complete', 'status': record['status'], 'count': len(record['results']),
          'error': record.get('error'),'network_before':record.get('network_before'),
          'network_after':record.get('network_after')})
    return 0 if record['status'] == 'PASS' else 2


if __name__ == '__main__':
    sys.exit(main())
