"""Bounded original-image wire and exact v0.7.0 preprocessing, without files."""
import hashlib
import io
import struct
import time

HEADER = struct.Struct('!4sII32s')
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def read_exact(stream, length):
    first=stream.read(length)
    if len(first)==length:
        return first
    if not first:
        raise EOFError('Truncated image stream')
    parts = bytearray(first)
    while len(parts) < length:
        block = stream.read(length - len(parts))
        if not block:
            raise EOFError('Truncated image stream')
        parts.extend(block)
    return bytes(parts)


def request(kind, index=0, size=0, digest=bytes(32)):
    if kind not in (b'BEG1', b'IMG1', b'END1', b'BYE1'):
        raise ValueError('Unknown message type')
    return HEADER.pack(kind, index, size, digest)


def read_message(stream,details=None):
    started = time.perf_counter_ns()
    details=details if details is not None else {}
    header=read_exact(stream,HEADER.size)
    mark=time.perf_counter_ns();details['header_read_ns']=mark-started
    kind,index,size,digest=HEADER.unpack(header)
    details['header_unpack_ns']=time.perf_counter_ns()-mark
    if kind == b'IMG1':
        if not 0 < size <= MAX_IMAGE_BYTES:
            raise ValueError('Image size out of bounds')
        mark=time.perf_counter_ns();data=read_exact(stream,size)
        details['body_read_ns']=time.perf_counter_ns()-mark
        mark=time.perf_counter_ns();actual=hashlib.sha256(data).digest()
        if actual != digest:
            raise ValueError('Original image SHA256 mismatch')
        details['wire_sha_check_ns']=time.perf_counter_ns()-mark
        details['source_sha256']=actual.hex()
    elif kind in (b'BEG1', b'END1', b'BYE1'):
        if digest != bytes(32) or (kind != b'BEG1' and size != 0):
            raise ValueError('Invalid control header')
        data = None
    else:
        raise ValueError('Unknown image-stream magic')
    return kind, index, size, data, started, time.perf_counter_ns()


def prepare_bytes(data, model):
    """Same Pillow/float32/round-half-away computation as prepare_image."""
    import numpy as np
    from PIL import Image
    from .preprocess import quantize_input,validate_input
    started = time.perf_counter_ns()
    timings = {}
    with Image.open(io.BytesIO(data)) as image:
        # convert L intentionally retains v0.7.0's lazy decode semantics.
        mark = time.perf_counter_ns()
        gray = image.convert('L')
        timings['decode_grayscale_ns'] = time.perf_counter_ns() - mark
        mark = time.perf_counter_ns()
        bilinear = Image.Resampling.BILINEAR if hasattr(Image, 'Resampling') else Image.BILINEAR
        gray = gray.resize((model.image_size, model.image_size), bilinear)
        timings['resize_ns'] = time.perf_counter_ns() - mark
        mark = time.perf_counter_ns()
        pixels = np.asarray(gray, dtype=np.uint8).copy()
        normalized = pixels.astype(np.float32) / 255.0
        timings['array_normalize_ns'] = time.perf_counter_ns() - mark
    mark = time.perf_counter_ns()
    payload = quantize_input(normalized, model.input_scale).reshape(1, 1, 64, 64).tobytes(order='C')
    validate_input(payload)
    timings['quantize_validate_ns'] = time.perf_counter_ns() - mark
    timings['preprocess_ns'] = time.perf_counter_ns() - started
    return payload, timings
