"""Background adapter for the existing v0.3.0 SSH service. No Tk calls here."""
import os
import hashlib
import json
import math
import queue
import shlex
import sys
import threading
import time
from pathlib import Path

STREAM = Path(__file__).resolve().parent.parent / 'stream'
sys.path.insert(0, str(STREAM))


class BoardAdapter:
    def __init__(self):
        self.events = queue.Queue()
        self.client = self.channel = None
        self.streams = None
        self.stop = threading.Event()
        self.run_id = 0
        self.ready = False

    def launch(self, operation, *args):
        def work():
            try:
                operation(*args)
            except Exception as exc:
                self.abort()
                self.events.put(('error', str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def connect(self, host, password):
        from tools.board_connection import connect
        from tools.client_images import Receiver
        from pscnn.runtime_config import stream_root, service_command
        started = time.perf_counter()
        client, secret, _ = connect(host, password=password)
        self.client = client
        if self.stop.is_set():
            client.close()
            return
        client.get_transport().set_keepalive(20)
        self.events.put(('connected', None))
        remote = stream_root()
        out = remote + '/reports/demo_' + str(time.time_ns())
        command = service_command(out, full_wire_report=True)
        # Keep stdin alive: ChannelStdinFile finalization sends SSH EOF.
        self.streams = client.exec_command(command)
        self.channel = self.streams[1].channel
        self.channel.settimeout(None)
        self.channel.sendall((secret + '\n').encode())
        secret = None
        self.receiver = Receiver(self.channel, time.perf_counter_ns())
        self.events.put(('status', '正在初始化板端…'))
        ready = self.receiver.get('ready')['event']
        if self.stop.is_set():
            self.abort()
            return
        self.ready = True
        self.receiver.receipts.clear()
        self.events.put(('ready', dict(connection_s=time.perf_counter()-started,
                                       initialization_s=ready.get('startup_ns', 0)/1e9)))

    def process(self, path, stride, session_id):
        import cv2
        from pscnn.image_stream import request, MAX_IMAGE_BYTES
        capture = cv2.VideoCapture(path)
        folder = Path(__file__).resolve().parent / 'records'
        folder.mkdir(exist_ok=True)
        samples = []
        started = time.perf_counter()
        last_seq = None
        try:
            # Decode once to count actual samples; metadata can overstate frame count.
            total = 0
            while True:
                if self.stop.is_set():
                    raise RuntimeError('处理已取消')
                ok, _ = capture.read()
                if not ok:
                    break
                total += 1
                if total % 30 == 0:
                    self.events.put(('scan', total))
            if not total:
                raise ValueError('视频没有可解码帧')
            count = (total + stride - 1) // stride
            if count > 100000:
                raise ValueError('采样超过协议单批上限，请增大采样间隔')
            capture.release()
            capture = cv2.VideoCapture(path)
            self.events.put(('scan_complete', total))
            run = self.run_id
            self.run_id += 1
            self.channel.sendall(request(b'BEG1', run, count))
            self.receiver.get('run_start')
            with (folder / (session_id + '_inference.jsonl')).open('w', encoding='utf-8') as log:
                for frame in range(total):
                    if self.stop.is_set():
                        raise RuntimeError('处理已取消')
                    ok, image = capture.read()
                    if not ok:
                        raise ValueError('视频再次解码时提前结束')
                    if frame % stride:
                        continue
                    ok, jpeg = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 90])
                    if not ok:
                        raise ValueError('JPEG编码失败')
                    payload = jpeg.tobytes()
                    if not 0 < len(payload) <= MAX_IMAGE_BYTES:
                        raise ValueError('JPEG超出板端大小限制')
                    digest = hashlib.sha256(payload).digest()
                    index = len(samples)
                    send_started = time.perf_counter_ns()
                    self.channel.sendall(request(b'IMG1', index, len(payload), digest) + payload)
                    send_ns = time.perf_counter_ns()-send_started
                    result = self.receiver.get('image_result')['event']
                    # The full result is persisted below; avoid duplicating all
                    # historical video results inside the measurement receiver.
                    if hasattr(self.receiver, 'receipts'):
                        self.receiver.receipts.clear()
                    native = result['native']
                    if (result['run_id'] != run or result['frame_id'] != index
                            or result['source_sha256'] != digest.hex()
                            or not native['readback_verified'] or native['count_checks'] != 1024
                            or native['bytes_read'] != 4096 or native['bytes_written'] != 4096):
                        raise ValueError('板端结果身份或完整性检查失败')
                    seq = native['result_seq']
                    if last_seq is not None and seq != (last_seq + 1) & 0xffffffff:
                        raise ValueError('PL输出序号不连续')
                    last_seq = seq
                    decoded = result['decoded']
                    risk = decoded['risk_score']
                    angle = math.degrees(decoded['steering_value'])
                    if decoded['steering_units'] != 'rad_label_space' or not all(map(math.isfinite, [risk, angle])) or not 0 <= risk <= 1:
                        raise ValueError('模型返回值无效')
                    row = dict(session_id=session_id, video_frame_id=frame, protocol_frame_id=index,
                               run_id=run, risk=risk, steering_deg=angle, result=result,
                               source_bytes=len(payload), pc_send_ns=send_ns)
                    log.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
                    log.flush()
                    samples.append(row)
                    self.events.put(('progress', (len(samples), count)))
            self.channel.sendall(request(b'END1', run))
            self.receiver.get('run_complete')
            self.events.put(('complete', dict(session_id=session_id, samples=samples,
                                            total=total, stride=stride, elapsed=time.perf_counter()-started)))
        finally:
            capture.release()

    def abort(self):
        self.ready = False
        if self.channel:
            self.channel.close()
        if self.client:
            self.client.close()

    def close(self):
        self.stop.set()
        self.abort()
