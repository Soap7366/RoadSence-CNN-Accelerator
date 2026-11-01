"""Offline adapter checks; these do not certify hardware or timing."""
import hashlib
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import gc
import sys
import cv2
import numpy as np
from board_adapter import BoardAdapter


class Wire:
    def __init__(self):
        self.digest = None
        self.bad_digest = False
    def sendall(self, data):
        if data[:4] == b'IMG1':
            self.digest = hashlib.sha256(data[44:]).hexdigest()
            assert data[12:44].hex() == self.digest


class Responses:
    def __init__(self, wire):
        self.wire = wire
        self.index = 0
    def get(self, event):
        if event != 'image_result':
            return {'event': {'event': event}}
        index = self.index
        self.index += 1
        return {'event': dict(run_id=0, frame_id=index,
            source_sha256='wrong' if self.wire.bad_digest else self.wire.digest,
            native=dict(readback_verified=True, count_checks=1024,
                        bytes_read=4096, bytes_written=4096, result_seq=index,
                        start_to_completed_s=.001),
            decoded=dict(risk_score=.7, steering_value=.2, steering_units='rad_label_space'))}


class AdapterTests(unittest.TestCase):
    def run_video(self, bad=False, stride=2, total=5):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            path = folder / 'video.avi'
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 30, (64, 64))
            self.assertTrue(writer.isOpened())
            for i in range(total):
                writer.write(np.full((64, 64, 3), i*20, dtype=np.uint8))
            writer.release()
            adapter = BoardAdapter()
            adapter.channel = Wire()
            adapter.channel.bad_digest = bad
            adapter.receiver = Responses(adapter.channel)
            with patch('board_adapter.__file__', str(folder / 'board_adapter.py')):
                if bad:
                    with self.assertRaises(ValueError):
                        adapter.process(str(path), 2, 'test')
                    self.assertFalse(any(k == 'complete' for k, _ in list(adapter.events.queue)))
                    return
                adapter.process(str(path), stride, 'test')
            kind, data = list(adapter.events.queue)[-1]
            self.assertEqual(kind, 'complete')
            frames = list(range(0, total, stride))
            self.assertEqual([r['video_frame_id'] for r in data['samples']], frames)
            self.assertEqual([r['protocol_frame_id'] for r in data['samples']], list(range(len(frames))))
            self.assertEqual(data['stride'], stride)
            self.assertEqual(data['total'], total)
            self.assertAlmostEqual(data['samples'][0]['steering_deg'], 11.459155902616466)
            self.assertEqual(len((folder/'records/test_inference.jsonl').read_text().splitlines()), len(frames))
    def test_connection_keeps_stdin_alive(self):
        closed = []
        class Stdin:
            def __del__(self):
                closed.append(True)
        class Channel:
            def settimeout(self, timeout):
                pass
            def sendall(self, data):
                if closed:
                    raise OSError('Socket is closed')
        channel = Channel()
        client = SimpleNamespace(get_transport=lambda: SimpleNamespace(set_keepalive=lambda seconds: None),
                                 exec_command=lambda command:
                                 (Stdin(), SimpleNamespace(channel=channel), object()))
        connection = SimpleNamespace(connect=lambda *a, **k: (client, 'test-secret', 0))
        receiver = SimpleNamespace(Receiver=lambda *a: SimpleNamespace(receipts=[], get=lambda event: {'event': {'startup_ns': 1000000000}}))
        adapter = BoardAdapter()
        with patch.dict(sys.modules, {'tools.board_connection': connection,
                                     'tools.client_images': receiver}):
            adapter.connect('test-host', 'test-secret')
        gc.collect()
        self.assertFalse(closed)
        self.assertTrue(adapter.ready)
        self.assertIs(adapter.channel, channel)
        del adapter.streams
        gc.collect()
        self.assertTrue(closed)

    def test_sampling_and_records(self):
        self.run_video()
    def test_four_frame_sampling_and_playback_mapping(self):
        from desktop import Demo
        for total in (1, 3, 4, 5, 10):
            with self.subTest(total=total):
                self.run_video(stride=4, total=total)
        frames = [0, 4, 8]
        samples = [dict(video_frame_id=frame) for frame in frames]
        player = SimpleNamespace(inference_frames=frames, inference_samples=samples)
        # Include reverse seeks and the final incomplete group.
        for frame in (0, 3, 4, 7, 8, 9, 2, 6, 0):
            self.assertEqual(Demo.held_sample(player, frame)['video_frame_id'], frame//4*4)
    def test_wrong_image_is_rejected(self):
        self.run_video(True)


if __name__ == '__main__':
    unittest.main()
