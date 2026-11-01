import hashlib
import io
import tempfile
import unittest
import queue
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from PIL import Image
from pscnn.image_stream import request, read_message, read_exact, prepare_bytes, MAX_IMAGE_BYTES
from pscnn.model import ModelSpec
from pscnn.preprocess import prepare_image


class StreamTests(unittest.TestCase):
    def test_original_preprocess_identical(self):
        model = ModelSpec.load()
        with tempfile.TemporaryDirectory() as directory:
            for mode in ('RGB', 'L'):
                path = Path(directory) / (mode + '.jpg')
                Image.new(mode, (960, 720), (59, 147, 240) if mode == 'RGB' else 79).save(path)
                old = prepare_image(path, model)[1].tobytes()
                new, _ = prepare_bytes(path.read_bytes(), model)
                self.assertEqual(new, old)

    def test_partial_reads(self):
        class Partial(io.BytesIO):
            def read(self, n=-1):
                return super().read(min(n, 3))
        data = b'original JPEG bytes'
        stream = Partial(request(b'IMG1', 7, len(data), hashlib.sha256(data).digest()) + data)
        kind, index, length, payload, _, _ = read_message(stream)
        self.assertEqual((kind, index, length, payload), (b'IMG1', 7, len(data), data))

    def test_corrupt_payload_rejected(self):
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            read_message(io.BytesIO(request(b'IMG1', 0, 1, bytes(32)) + b'x'))

    def test_oversize_rejected_before_payload(self):
        with self.assertRaisesRegex(ValueError, 'bounds'):
            read_message(io.BytesIO(request(b'IMG1', 0, MAX_IMAGE_BYTES+1)))

    def test_truncated_header_or_payload(self):
        for data in (b'I', request(b'IMG1', 0, 5) + b'one'):
            with self.assertRaises(EOFError):
                read_message(io.BytesIO(data))

    def test_control_frame_contract(self):
        with self.assertRaises(ValueError):
            read_message(io.BytesIO(request(b'END1', 0, 3)))
        for kind in (b'BEG1', b'END1', b'BYE1'):
            self.assertEqual(read_message(io.BytesIO(request(kind)))[0], kind)

    def test_bad_image_does_not_infer(self):
        with self.assertRaises(Exception):
            prepare_bytes(b'not a JPEG', ModelSpec.load())

    def test_bounded_pipeline_keeps_two_batches_ordered(self):
        from tools.serve_images import pipeline
        data = io.BytesIO()
        Image.new('RGB', (90, 60), (20, 40, 60)).save(data, format='JPEG')
        data = data.getvalue()
        wire = bytearray()
        for run in range(2):
            wire += request(b'BEG1', run, 2)
            for index in range(2):
                wire += request(b'IMG1', index, len(data), hashlib.sha256(data).digest()) + data
            wire += request(b'END1', run)
        wire += request(b'BYE1')
        raw, prepared, failed = queue.Queue(2), queue.Queue(2), queue.Queue()
        with patch('tools.serve_images.sys.stdin', SimpleNamespace(buffer=io.BytesIO(wire))):
            pipeline(ModelSpec.load(), raw, prepared, failed)
            output = [prepared.get(timeout=3) for _ in range(9)]
        self.assertTrue(failed.empty())
        self.assertEqual([x[0] for x in output], [b'BEG1', b'IMG1', b'IMG1', b'END1']*2+[b'BYE1'])
        for message in output:
            if message[0] == b'IMG1':
                message=message[3].result(timeout=3)
                self.assertEqual(len(message[3]), 4096)
                self.assertEqual(message[5], hashlib.sha256(data).hexdigest())

    def test_pipeline_rejects_image_before_begin(self):
        from tools.serve_images import pipeline
        data = b'invalid order'; wire = request(b'IMG1', 0, len(data), hashlib.sha256(data).digest()) + data
        raw, prepared, failed = queue.Queue(2), queue.Queue(2), queue.Queue()
        with patch('tools.serve_images.sys.stdin', SimpleNamespace(buffer=io.BytesIO(wire))):
            pipeline(ModelSpec.load(), raw, prepared, failed)
            error = failed.get(timeout=3)
        self.assertIn('Out of order', str(error))
        self.assertTrue(prepared.empty())


if __name__ == '__main__':
    unittest.main()
