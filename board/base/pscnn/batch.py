"""Directory image batches using the unchanged input contract and one session.

This module never imports PYNQ, downloads an Overlay, or runs Golden samples.
The hardware runner owns initialization, the device lock and the one-time
Golden self-check. Input images are read only; every batch uses a new directory.
"""
import copy
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .bundle import load_prepared, save_prepared
from .postprocess import checked_int32, decode_outputs

IMAGE_EXTENSIONS = frozenset((".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"))


def _is_link(path):
    # On Windows a junction is not always reported as a symbolic link.
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (callable(is_junction) and is_junction())


def _input_root(input_dir):
    root = Path(input_dir).expanduser()
    if _is_link(root):
        raise ValueError("Input directory must not be a symbolic link or junction")
    if not root.is_dir():
        raise ValueError("Input directory does not exist or is not a directory: " + str(root))
    return root.resolve()


def discover_images(input_dir, recursive=False):
    """Return a fixed, deterministic list of supported image paths.

    Extensions are case-insensitive. Symbolic links and junctions are skipped,
    including linked directories. Decoding is deferred to the per-image stage
    so that one damaged image does not discard the rest of a batch.
    """
    if type(recursive) is not bool:
        raise ValueError("recursive must be bool")
    root = _input_root(input_dir)
    images = []
    if recursive:
        def walk_error(error):
            raise error

        for directory, names, files in os.walk(str(root), followlinks=False, onerror=walk_error):
            parent = Path(directory)
            names[:] = [name for name in names if not _is_link(parent / name)]
            for name in files:
                path = parent / name
                if path.suffix.lower() in IMAGE_EXTENSIONS and not _is_link(path) and path.is_file():
                    images.append(path)
    else:
        images = [
            path for path in root.iterdir()
            if path.suffix.lower() in IMAGE_EXTENSIONS and not _is_link(path) and path.is_file()
        ]
    if not images:
        raise ValueError("No supported images found in input directory: " + str(root))
    return sorted(images, key=lambda path: (
        path.relative_to(root).as_posix().casefold(), path.relative_to(root).as_posix()))


def _snapshot_paths(paths, root):
    result, seen = [], set()
    for value in paths:
        path = Path(value)
        if not path.is_absolute():
            path = root / path
        # Lexical normalization keeps link detection possible at processing time.
        path = Path(os.path.abspath(str(path)))
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise ValueError("Batch image path is outside input directory: " + str(path)) from exc
        if not relative.parts:
            raise ValueError("A batch image path must name a file")
        if path in seen:
            raise ValueError("Duplicate image path in batch: " + str(path))
        seen.add(path)
        result.append((path, relative))
    if not result:
        raise ValueError("A batch must contain at least one image")
    return tuple(result)


def _check_source(path, relative, root):
    current = root
    for part in relative.parts:
        current = current / part
        if _is_link(current):
            raise ValueError("Batch images must not use symbolic links or junctions: " + str(path))
    try:
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise ValueError("Batch image resolved outside input directory: " + str(path)) from exc
    if not path.is_file():
        raise FileNotFoundError("Image no longer exists or is not a regular file: " + str(path))


def _write_new_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())


def _last_run(session):
    report = getattr(session, "last_run", None)
    return copy.deepcopy(report) if isinstance(report, dict) else None


def run_image_batch(paths, input_dir, out_dir, model, session, timeout_s=15.0, emit=None):
    """Prepare and infer each image through one caller-owned control session.

    Preparation failures are recorded and processing continues. Any inference
    or output-decoding failure stops the batch, even if the session recovered
    automatically: the runner must explicitly investigate before another job.
    Completed items are durable before their notification is emitted.
    """
    if isinstance(timeout_s, bool):
        raise ValueError("timeout_s must be finite and positive")
    timeout_s = float(timeout_s)
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout_s must be finite and positive")
    if emit is not None and not callable(emit):
        raise ValueError("emit must be callable or None")
    if not callable(getattr(session, "infer", None)):
        raise ValueError("session must provide infer()")
    root = _input_root(input_dir)
    snapshot = _snapshot_paths(paths, root)
    directory = Path(out_dir).resolve()
    if directory == root:
        raise ValueError("Batch output must be a new directory")
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "items").mkdir()
    began = time.monotonic()
    summary = {
        "schema": "pscnn.image-batch.v1",
        "status": "FAIL",
        "reported_utc": datetime.now(timezone.utc).isoformat(),
        "board_clock_verified": False,
        "model_id": model.model_id,
        "quant_params_sha256": model.quant_sha256,
        "input_dir": str(root),
        "output_dir": str(directory),
        "total": len(snapshot),
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "remaining": len(snapshot),
        "stopped_early": False,
        "readback_required": True,
        "items": [],
        "elapsed_s": 0.0,
        "timing_note": "Per-image total excludes result-file persistence and notification.",
    }
    with (directory / "results.jsonl").open("x", encoding="utf-8", newline="\n") as records:
        for index, (path, relative) in enumerate(snapshot, 1):
            started = time.monotonic()
            item_dir = directory / "items" / "{:06d}".format(index)
            item_dir.mkdir()
            prepared = item_dir / "prepared"
            item = {
                "index": index,
                "source_path": str(path),
                "source_relative": relative.as_posix(),
                "prepared_dir": str(prepared),
                "status": "FAIL",
                "phase": "prepare",
                "hardware_accessed": False,
                "inference_performed": False,
                "raw": None,
                "decoded": None,
                "control_run": None,
                "prepare_elapsed_s": 0.0,
                "total_elapsed_s": 0.0,
            }
            stop = False
            prepare_began = time.monotonic()
            try:
                _check_source(path, relative, root)
                metadata = save_prepared(path, prepared, model)
                payload, validation = load_prepared(prepared, model, path)
                item["source_image_sha256"] = metadata["source_image_sha256"]
                item["input_int8_sha256"] = validation["input_int8_sha256"]
                item["prepare_elapsed_s"] = max(0.0, time.monotonic() - prepare_began)
                item["phase"] = "infer"
                item["hardware_accessed"] = True
                try:
                    raw = session.infer(payload, timeout_s=timeout_s, verify_readback=True)
                finally:
                    item["control_run"] = _last_run(session)
                    if item["control_run"]:
                        item["inference_performed"] = bool(item["control_run"].get("start_accepted"))
                item["inference_performed"] = True
                item["phase"] = "decode"
                item["raw"] = {
                    "collision_int32": checked_int32(raw["collision_int32"]),
                    "steering_int32": checked_int32(raw["steering_int32"]),
                }
                item["decoded"] = decode_outputs(
                    item["raw"]["collision_int32"], item["raw"]["steering_int32"], model)
                item["status"], item["phase"] = "PASS", "complete"
            except Exception as exc:
                item["failed_phase"] = item["phase"]
                item["error"] = {"type": type(exc).__name__, "message": str(exc)}
                if item["phase"] == "prepare":
                    item["prepare_elapsed_s"] = max(0.0, time.monotonic() - prepare_began)
                else:
                    stop = True
            item["total_elapsed_s"] = max(0.0, time.monotonic() - started)
            _write_new_json(item_dir / "result.json", item)
            records.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")
            records.flush()
            os.fsync(records.fileno())
            summary["items"].append(item)
            summary["processed"] += 1
            summary["succeeded" if item["status"] == "PASS" else "failed"] += 1
            summary["remaining"] = summary["total"] - summary["processed"]
            summary["stopped_early"] = stop
            if emit is not None:
                emit(copy.deepcopy(item))
            if stop:
                break
    summary["status"] = (
        "PASS" if summary["failed"] == 0 and summary["remaining"] == 0
        else "PARTIAL" if summary["succeeded"] else "FAIL")
    summary["elapsed_s"] = max(0.0, time.monotonic() - began)
    _write_new_json(directory / "summary.json", summary)
    return summary
