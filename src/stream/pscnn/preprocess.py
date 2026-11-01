"""Deployment preprocessing, matching dual_task_lite/dual_dataset.py."""
from pathlib import Path
import numpy as np
from PIL import Image


def round_half_away_from_zero(values):
    values = np.asarray(values, dtype=np.float64)
    if not np.all(np.isfinite(values)):
        raise ValueError("Non-finite values cannot be quantized")
    return np.sign(values) * np.floor(np.abs(values) + 0.5)


def quantize_input(values, scale):
    # Keep the source reference's float32 division before its rounding helper.
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("Scale must be finite and positive")
    scaled = np.asarray(values, dtype=np.float32) / float(scale)
    return np.clip(round_half_away_from_zero(scaled), -127, 127).astype(np.int8)


def prepare_image(path, model):
    with Image.open(Path(path)) as image:
        grayscale = image.convert("L")
        bilinear = Image.Resampling.BILINEAR if hasattr(Image, "Resampling") else Image.BILINEAR
        grayscale = grayscale.resize((model.image_size, model.image_size), bilinear)
        pixels = np.asarray(grayscale, dtype=np.uint8).copy()
    # No EXIF transpose, crop, padding, augmentation or per-image min/max.
    normalized = pixels.astype(np.float32) / 255.0
    tensor = quantize_input(normalized, model.input_scale).reshape(1, 1, 64, 64)
    return pixels, tensor


def validate_input(payload):
    if len(payload) != 4096:
        raise ValueError("Input must contain exactly 4096 bytes; got {}".format(len(payload)))
    # This model's grayscale input is non-negative despite its signed INT8 storage.
    if np.any(np.frombuffer(payload, dtype=np.int8) < 0):
        raise ValueError("Grayscale input must be INT8 values in [0, 127]")
    return payload
