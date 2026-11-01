"""Portable image input bundles. No hardware addresses or PL writes."""
import json
import platform
import re
from pathlib import Path
import numpy as np
import PIL
from PIL import Image
from .model import sha256
from .preprocess import prepare_image, quantize_input, validate_input

SCHEMA = "pscnn.input.v1"


def save_prepared(image_path, out_dir, model):
    image_path, out_dir = Path(image_path), Path(out_dir)
    source_sha = sha256(image_path.read_bytes())
    pixels, tensor = prepare_image(image_path, model)
    if sha256(image_path.read_bytes()) != source_sha:
        raise ValueError("Source image changed during preparation")
    data = validate_input(tensor.tobytes(order="C"))
    hex_text = "".join("{:02X}\n".format(value) for value in data)
    metadata = {
        "schema": SCHEMA, "model_id": model.model_id,
        "quant_params_sha256": model.quant_sha256,
        "source_image": str(image_path.resolve()), "source_image_sha256": source_sha,
        "shape": [1, 1, 64, 64], "dtype": "int8",
        "layout": "NCHW; row-major; one byte per pixel", "bytes": len(data),
        "input_scale": model.input_scale, "zero_point": 0,
        "min": int(tensor.min()), "max": int(tensor.max()),
        "input_int8_sha256": sha256(data), "input_hex_sha256": sha256(hex_text.encode("ascii")),
        "hex_layout": "4096 lines; one byte per line; logical CHW addresses 0..4095",
        "preprocessing": "Pillow convert L -> BILINEAR 64x64 -> float32/255 -> half-away INT8",
        "python": platform.python_version(), "numpy": np.__version__, "Pillow": PIL.__version__,
        "hardware_verified": False, "pl_written": False,
    }
    out_dir.mkdir(parents=True, exist_ok=False)
    Image.fromarray(pixels).save(out_dir / "gray64.png")
    metadata["gray64_sha256"] = sha256((out_dir / "gray64.png").read_bytes())
    with (out_dir / "input_int8.bin").open("xb") as f:
        f.write(data)
    with (out_dir / "input_int8.hex").open("x", encoding="ascii", newline="\n") as f:
        f.write(hex_text)
    with (out_dir / "metadata.json").open("x", encoding="utf-8", newline="\n") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
    return {"output_dir": str(out_dir.resolve()), **metadata}


def load_prepared(directory, model, source_image=None):
    directory = Path(directory)
    m = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    if not isinstance(m, dict):
        raise ValueError("Input metadata must be a JSON object")
    fields = {"schema": SCHEMA, "model_id": model.model_id,
              "quant_params_sha256": model.quant_sha256, "shape": [1, 1, 64, 64],
              "dtype": "int8", "bytes": 4096, "input_scale": model.input_scale,
              "zero_point": 0, "layout": "NCHW; row-major; one byte per pixel"}
    for name, expected in fields.items():
        if m.get(name) != expected:
            raise ValueError("Input bundle contract mismatch: " + name)
    data = validate_input((directory / "input_int8.bin").read_bytes())
    if sha256(data) != m.get("input_int8_sha256"):
        raise ValueError("Input binary SHA256 mismatch")
    values = np.frombuffer(data, dtype=np.int8)
    if m.get("min") != int(values.min()) or m.get("max") != int(values.max()):
        raise ValueError("Input min/max mismatch")
    hex_data = (directory / "input_int8.hex").read_bytes()
    if sha256(hex_data) != m.get("input_hex_sha256"):
        raise ValueError("Input HEX SHA256 mismatch")
    lines = hex_data.decode("ascii").splitlines()
    if len(lines) != 4096 or any(not re.fullmatch("[0-9A-Fa-f]{2}", x) for x in lines):
        raise ValueError("HEX must contain exactly 4096 lines of one byte each")
    if bytes.fromhex("".join(lines)) != data:
        raise ValueError("HEX bytes differ from binary")
    gray = directory / "gray64.png"
    if sha256(gray.read_bytes()) != m.get("gray64_sha256"):
        raise ValueError("Gray image SHA256 mismatch")
    with Image.open(gray) as image:
        if image.mode != "L" or image.size != (64, 64):
            raise ValueError("Gray image must be mode L and size 64x64")
        pixels = np.asarray(image, dtype=np.uint8).copy()
    if quantize_input(pixels.astype(np.float32) / 255.0, model.input_scale).tobytes() != data:
        raise ValueError("Gray image does not quantize to stored binary")
    if source_image is not None:
        source_image = Path(source_image)
        if sha256(source_image.read_bytes()) != m.get("source_image_sha256"):
            raise ValueError("Source image SHA256 mismatch")
        source_pixels, source_tensor = prepare_image(source_image, model)
        if not np.array_equal(source_pixels, pixels) or source_tensor.tobytes() != data:
            raise ValueError("Source preprocessing differs from prepared input")
    return data, {
        "status": "PASS", "schema": SCHEMA, "model_id": model.model_id,
        "directory": str(directory.resolve()), "bytes": len(data),
        "input_int8_sha256": sha256(data), "min": int(values.min()), "max": int(values.max()),
        "hex_entries": 4096, "source_image_verified": source_image is not None,
        "input_payload_valid": True, "pl_written": False, "inference_performed": False,
    }
