"""Validate a local bit/HWH bundle without importing PYNQ or accessing devices."""
import hashlib
import json
import math
import re
import time
import xml.etree.ElementTree as ET
from numbers import Real
from pathlib import Path, PurePosixPath, PureWindowsPath

MODEL_ID = "dual_task_baseline2_int8_qat@619308c77fd9"
MODEL_COMMIT = "619308c77fd9af7d03bc89c494a33dda5d07386d"
RTL_COMMIT = "8c8dcfafc901f0c54276e6aaba86d817cb82538d"
PART = "xc7z020clg400-1"
ABI_VERSION = 0x00010000
MAGIC = 0x53434E4E
IP_NAME = "cnn_axi_accelerator_0"


def checked_relative_file(directory, name):
    """Resolve a manifest child file and reject absolute paths or directory escapes."""
    directory = Path(directory).resolve()
    if not isinstance(name, str) or not name:
        raise ValueError("Artifact file path must be a nonempty relative string")
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or PureWindowsPath(name).drive or ".." in path.parts:
        raise ValueError("Artifact file path must stay inside the manifest directory")
    resolved = (directory / Path(*path.parts)).resolve()
    if directory not in resolved.parents or not resolved.is_file():
        raise ValueError("Artifact file is missing or escapes the manifest directory: " + name)
    return resolved


def _nonnegative_number(value, field):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)) or value < 0:
        raise ValueError(field + " must be finite and nonnegative")


def _file_info(root, entry, kind, timings):
    if not isinstance(entry, dict):
        raise ValueError("Missing artifact file descriptor: " + kind)
    size, digest = entry.get("bytes"), entry.get("sha256")
    if type(size) is not int or size <= 0:
        raise ValueError(kind + " bytes must be a positive integer")
    if not isinstance(digest, str) or not re.fullmatch("[0-9a-fA-F]{64}", digest):
        raise ValueError(kind + " SHA256 must contain 64 hex digits")
    path = checked_relative_file(root, entry.get("path"))
    mark = time.perf_counter_ns()
    data = path.read_bytes()
    timings[kind + '_read_ns'] = time.perf_counter_ns() - mark
    mark = time.perf_counter_ns()
    actual = hashlib.sha256(data).hexdigest()
    timings[kind + '_sha256_ns'] = time.perf_counter_ns() - mark
    if len(data) != size or actual != digest.lower():
        raise ValueError(kind + " file bytes/SHA256 mismatch")
    return path, data, actual


def _hwh_mapping(data, ip_name):
    try:
        document = ET.fromstring(data)
    except (ET.ParseError, ValueError) as exc:
        raise ValueError("Cannot parse HWH XML: " + str(exc)) from exc
    mappings = []
    for element in document.iter():
        if element.tag.rsplit("}", 1)[-1].upper() != "MEMRANGE":
            continue
        attributes = {key.upper(): value for key, value in element.attrib.items()}
        if attributes.get("INSTANCE") != ip_name:
            continue
        try:
            low = int(attributes["BASEVALUE"], 0)
            high = int(attributes["HIGHVALUE"], 0)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("HWH target MEMRANGE has invalid BASEVALUE/HIGHVALUE") from exc
        if not 0 <= low <= high <= 0xFFFFFFFF or low % 4:
            raise ValueError("HWH target address range is invalid")
        mappings.append((low, high - low + 1))
    unique = set(mappings)
    if len(unique) != 1:
        raise ValueError("HWH must contain exactly one unambiguous target base/range")
    base, size = next(iter(unique))
    if size < 8192:
        raise ValueError("HWH target range is smaller than the 8192-byte bridge window")
    return base, size, len(mappings)


def validate_overlay(manifest_path, model, timings=None):
    """Check identity, build gates, hashes and HWH mapping; return JSON-safe details."""
    timings = timings if timings is not None else {}
    mark = time.perf_counter_ns()
    manifest_path = Path(manifest_path).resolve()
    payload = manifest_path.read_bytes()
    manifest = json.loads(payload.decode("utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Overlay manifest must be a JSON object")
    expected = {
        "schema": "pscnn.overlay.v1", "model_id": model.model_id,
        "quant_params_sha256": model.quant_sha256, "model_commit": MODEL_COMMIT,
        "rtl_commit": RTL_COMMIT, "part": PART, "ip_name": IP_NAME,
    }
    if model.model_id != MODEL_ID:
        raise ValueError("Overlay validation is pinned to model " + MODEL_ID)
    for name, value in expected.items():
        if manifest.get(name) != value:
            raise ValueError("Overlay manifest identity mismatch: " + name)
    for name, value in (("abi_version", ABI_VERSION), ("magic", MAGIC)):
        if type(manifest.get(name)) is not int or manifest[name] != value:
            raise ValueError("Overlay manifest identity mismatch: " + name)
    clock = manifest.get("clock_mhz")
    if isinstance(clock, bool) or not isinstance(clock, Real) or not math.isfinite(float(clock)) or clock != 50:
        raise ValueError("Overlay clock_mhz must be 50")
    build = manifest.get("build_verified")
    if not isinstance(build, dict):
        raise ValueError("Overlay manifest requires build_verified evidence")
    for name in ("setup_slack_ns", "hold_slack_ns"):
        _nonnegative_number(build.get(name), name)
    for name, minimum in (("rtl_golden_samples", 30), ("protocol_groups", 9)):
        if type(build.get(name)) is not int or build[name] < minimum:
            raise ValueError("Overlay build gate failed: " + name)
    declared_base, declared_range = manifest.get("hwh_phys_addr"), manifest.get("hwh_addr_range")
    if type(declared_base) is not int or not 0 <= declared_base <= 0xFFFFFFFF or declared_base % 4:
        raise ValueError("Overlay hwh_phys_addr must be an aligned unsigned 32-bit address")
    if type(declared_range) is not int or declared_range < 8192:
        raise ValueError("Overlay hwh_addr_range must be at least 8192")
    timings['manifest_parse_identity_ns'] = time.perf_counter_ns() - mark
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ValueError("Overlay files must be an object")
    bit, bit_data, bit_hash = _file_info(manifest_path.parent, files.get("bit"), "bit", timings)
    hwh, hwh_data, hwh_hash = _file_info(manifest_path.parent, files.get("hwh"), "hwh", timings)
    if bit.suffix != ".bit" or hwh.suffix != ".hwh" or bit.with_suffix(".hwh") != hwh:
        raise ValueError("The bit and HWH must be adjacent files with the same stem")
    mark = time.perf_counter_ns()
    base, size, matches = _hwh_mapping(hwh_data, IP_NAME)
    if base != declared_base or size != declared_range:
        raise ValueError("HWH base/range differs from the overlay manifest")
    timings['hwh_xml_address_ns'] = time.perf_counter_ns() - mark
    return {
        "schema": "pscnn.overlay.validated.v1", "gates_passed": True,
        "manifest_path": str(manifest_path), "manifest_sha256": hashlib.sha256(payload).hexdigest(),
        "bit_path": str(bit), "hwh_path": str(hwh),
        "bit_sha256": bit_hash, "hwh_sha256": hwh_hash,
        "bit_bytes": len(bit_data), "hwh_bytes": len(hwh_data),
        "ip_name": IP_NAME, "phys_addr": base, "addr_range": size,
        "base_address_source": "validated HWH MEMRANGE",
        "hwh_matching_memranges": matches, "model_id": model.model_id,
        "quant_params_sha256": model.quant_sha256, "model_commit": MODEL_COMMIT,
        "rtl_commit": RTL_COMMIT, "part": PART, "clock_mhz": 50,
        "abi_version": ABI_VERSION, "magic": MAGIC,
        "build_verified": dict(build), "hardware_accessed": False,
    }
