"""Explicit hardware runner. Preflight is the default; --execute enables download.

No simulation fallback, network changes, package installation or privilege changes.
Use only with an exclusively owned PYNQ-Z2 and the validated local bridge bundle.
"""
import argparse
import hashlib
import json
import math
import platform
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from numbers import Integral
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pscnn import __version__
from pscnn.model import ModelSpec
from pscnn.overlay_artifact import validate_overlay, checked_relative_file
from pscnn.postprocess import checked_int32, decode_outputs
from pscnn.boot_state import load_boot_state

DEVICE_LOCK = "/tmp/pscnn_axi_device.lock"


def load_hardware_golden(manifest_path, model):
    """Freeze and hash-check all thirty INT8 samples before any hardware activity."""
    manifest_path = Path(manifest_path).resolve()
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw.decode("utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Hardware Golden manifest must be an object")
    for name, expected in (("schema", "pscnn.hardware-golden.v1"),
                           ("model_id", model.model_id),
                           ("quant_params_sha256", model.quant_sha256)):
        if manifest.get(name) != expected:
            raise ValueError("Hardware Golden contract mismatch: " + name)
    entries = manifest.get("samples")
    if not isinstance(entries, list) or len(entries) != 30:
        raise ValueError("Hardware Golden must contain exactly thirty samples")
    samples, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict) or type(entry.get("id")) is not int:
            raise ValueError("Hardware Golden sample id must be an integer")
        identifier = entry["id"]
        if identifier in seen or not 0 <= identifier < 30:
            raise ValueError("Hardware Golden sample ids must cover 0..29 exactly once")
        seen.add(identifier)
        path = checked_relative_file(manifest_path.parent, entry.get("input_file"))
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if entry.get("input_sha256") != digest:
            raise ValueError("Hardware Golden input SHA256 mismatch for sample " + str(identifier))
        if len(data) != 4096 or any(value > 127 for value in data):
            raise ValueError("Hardware Golden input must contain 4096 INT8 bytes in [0,127]")
        samples.append({
            "id": identifier, "input_file": str(path), "input_sha256": digest, "payload": data,
            "collision_int32": checked_int32(entry.get("collision_int32")),
            "steering_int32": checked_int32(entry.get("steering_int32")),
        })
    return {
        "manifest_path": str(manifest_path), "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "sample_count": len(samples), "samples": sorted(samples, key=lambda sample: sample["id"]),
    }


@contextmanager
def _device_lock():
    # Importing this module or doing a PC preflight never imports fcntl or PYNQ.
    import fcntl
    with open(DEVICE_LOCK, "a+", encoding="ascii") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another process holds " + DEVICE_LOCK) from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _uint(value, name):
    if isinstance(value, bool) or not isinstance(value, Integral) or not 0 <= int(value) <= 0xFFFFFFFF:
        raise ValueError(name + " must be an unsigned 32-bit integer")
    return int(value)


def _ip_mapping(overlay, artifact):
    entry = overlay.ip_dict.get(artifact["ip_name"])
    if not isinstance(entry, dict):
        raise ValueError("PYNQ ip_dict does not contain " + artifact["ip_name"])
    base = _uint(entry.get("phys_addr"), "PYNQ phys_addr")
    size = _uint(entry.get("addr_range"), "PYNQ addr_range")
    if base != artifact["phys_addr"] or size != artifact["addr_range"] or size < 8192:
        raise ValueError("PYNQ ip_dict mapping differs from the validated HWH")
    return base, size


def _output_directory(value):
    directory = Path(value)
    directory = (directory if directory.is_absolute() else ROOT / directory).resolve()
    if ROOT.resolve() not in directory.parents:
        raise ValueError("Keep hardware reports inside this PS project")
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def _summary(report, directory):
    value = {
        "status": report["status"], "mode": report["mode"],
        "out_dir": str(directory), "hardware_accessed": report["hardware_accessed"],
        "inference_performed": report["inference_performed"],
        "golden_samples_validated": report.get("golden_samples_validated", 0),
        "golden_samples_passed": report.get("golden_samples_passed", 0),
        "photo_inferred": report.get("photo_result") is not None,
    }
    value["overlay_downloaded"] = report.get("overlay_downloaded", False)
    value["used_boot_loaded_overlay"] = report.get("used_boot_loaded_overlay", False)
    if report.get("photo_result") is not None:
        value["photo_result"] = report["photo_result"]["decoded"]
    if report.get("error"):
        value["error"] = report["error"]
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--golden-manifest", required=True)
    parser.add_argument("--prepared-dir")
    parser.add_argument("--source-image")
    parser.add_argument("--out-dir", required=True, help="A new directory inside this project")
    parser.add_argument("--timeout", type=float, default=15.0, help="Seconds per hardware transaction")
    parser.add_argument("--execute", action="store_true", help="Explicitly allow FPGA download and real inference")
    parser.add_argument("--use-loaded", action="store_true", help="Use this boot service\'s loaded PL; never download or fall back")
    args = parser.parse_args(argv)
    try:
        directory = _output_directory(args.out_dir)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "hardware_accessed": False, "error": str(exc)}))
        return 2
    began = time.monotonic()
    report = {
        "schema": "pscnn.hardware-run.v1", "framework_version": __version__,
        "status": "FAIL", "mode": "hardware_inference" if args.execute else "hardware_preflight",
        "phase": "arguments", "reported_utc": datetime.now(timezone.utc).isoformat(),
        "board_clock_verified": False, "hardware_accessed": False,
        "inference_attempted": False, "inference_performed": False, "inference_completed": False,
        "golden_results": [], "golden_samples_passed": 0, "photo_result": None,
        "overlay_downloaded": False, "used_boot_loaded_overlay": False,
        "runtime": {"python": platform.python_version(), "machine": platform.machine()},
        "timeout_s": args.timeout, "execute_requested": args.execute,
    }
    session = None
    try:
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            report["timeout_s"] = None
            raise ValueError("Timeout must be finite and positive")
        if args.use_loaded and not args.execute:
            raise ValueError("--use-loaded requires --execute")
        if args.source_image and not args.prepared_dir:
            raise ValueError("--source-image requires --prepared-dir")
        report["phase"] = "model"
        model = ModelSpec.load()
        report["model_id"], report["quant_params_sha256"] = model.model_id, model.quant_sha256
        report["phase"] = "validate_overlay"
        artifact = validate_overlay(args.manifest, model)
        report["overlay"] = artifact
        report["phase"] = "validate_all_golden_inputs"
        golden = load_hardware_golden(args.golden_manifest, model)
        report["golden_manifest"] = {name: golden[name] for name in ("manifest_path", "manifest_sha256", "sample_count")}
        report["golden_samples_validated"] = golden["sample_count"]
        report["golden_inputs"] = [{key: value for key, value in sample.items() if key != "payload"}
                                  for sample in golden["samples"]]
        photo = None
        if args.prepared_dir:
            report["phase"] = "validate_photo_input"
            from pscnn.bundle import load_prepared
            photo, input_report = load_prepared(args.prepared_dir, model, args.source_image)
            report["photo_input"] = input_report
            report["photo_input_sha256"] = hashlib.sha256(photo).hexdigest()
            if args.source_image:
                report["photo_source_path"] = str(Path(args.source_image).resolve())
                report["photo_source_sha256"] = hashlib.sha256(Path(args.source_image).read_bytes()).hexdigest()
        if not args.execute:
            report["phase"], report["status"] = "preflight_complete", "PREFLIGHT_PASS"
        else:
            report["phase"] = "device_lock"
            with _device_lock():
                report["device_lock_path"] = DEVICE_LOCK
                if args.use_loaded:
                    report["phase"] = "verify_boot_loaded_state"
                    report["boot_state"] = load_boot_state(artifact)
                report["phase"] = "import_pynq"
                import pynq
                from pscnn.axi_transport import AxiLiteTransport
                from pscnn.control import ControlSession
                report["runtime"]["pynq"] = getattr(pynq, "__version__", "unknown")
                report["phase"] = "parse_pynq_hwh"
                overlay = pynq.Overlay(artifact["bit_path"], download=False)
                base, size = _ip_mapping(overlay, artifact)
                report["phys_addr"], report["addr_range"] = base, size
                report["base_address_source"] = "validated HWH and matching PYNQ ip_dict"
                report["phase"] = "revalidate_before_download"
                again = validate_overlay(args.manifest, model)
                for key in ("manifest_sha256", "bit_sha256", "hwh_sha256", "phys_addr", "addr_range"):
                    if artifact[key] != again[key]:
                        raise ValueError("Overlay artifact changed after preflight: " + key)
                if args.use_loaded:
                    report["phase"] = "recheck_boot_loaded_state"
                    if load_boot_state(artifact) != report["boot_state"]:
                        raise ValueError("Boot state changed after loaded-PL preflight")
                    report["used_boot_loaded_overlay"] = True
                else:
                    report["phase"] = "download_overlay"
                    # A failing download may already have changed PL: record access first.
                    report["hardware_accessed"] = True
                    overlay.download()
                    report["overlay_downloaded"] = True
                report["phase"] = "map_mmio"
                report["hardware_accessed"] = True
                registers = pynq.MMIO(base, size)
                transport = AxiLiteTransport(registers, model)
                report["phase"] = "check_device_identity"
                identity = transport.describe(args.timeout)
                report["device_identity"] = {
                    "protocol": identity.protocol, "model_id": identity.model_id,
                    "quant_params_sha256": identity.quant_params_sha256, "input_bytes": identity.input_bytes,
                }
                session = ControlSession(transport, model)
                for sample in golden["samples"]:
                    report["phase"] = "golden_" + str(sample["id"])
                    report["inference_attempted"] = True
                    raw = session.infer(sample["payload"], timeout_s=args.timeout)
                    report["inference_performed"] = True
                    decoded = decode_outputs(raw["collision_int32"], raw["steering_int32"], model)
                    expected = {key: sample[key] for key in ("collision_int32", "steering_int32")}
                    passed = raw == expected
                    report["golden_results"].append({
                        "id": sample["id"], "input_sha256": sample["input_sha256"],
                        "mode": "hardware_inference", "hardware_accessed": True,
                        "raw": raw, "expected_raw": expected, "decoded": decoded,
                        "passed": passed, "control_run": dict(session.last_run),
                    })
                    if not passed:
                        raise RuntimeError("Hardware raw INT32 differs from Golden sample " + str(sample["id"]))
                    report["golden_samples_passed"] += 1
                if photo is not None:
                    report["phase"] = "photo_inference"
                    report["inference_attempted"] = True
                    raw = session.infer(photo, timeout_s=args.timeout)
                    report["inference_performed"] = True
                    report["photo_result"] = {
                        "mode": "hardware_inference", "hardware_accessed": True,
                        "input_sha256": hashlib.sha256(photo).hexdigest(), "raw": raw,
                        "decoded": decode_outputs(raw["collision_int32"], raw["steering_int32"], model),
                        "control_run": dict(session.last_run),
                    }
                report["inference_completed"] = True
                report["phase"], report["status"] = "complete", "PASS"
    except Exception as exc:
        report["failed_phase"] = report["phase"]
        report["error"] = {"type": type(exc).__name__, "message": str(exc), "phase": report["phase"]}
        if session is not None:
            report["failed_control_run"] = dict(session.last_run)
            report["inference_performed"] = bool(report["inference_performed"] or session.last_run.get("start_accepted"))
        report["phase"], report["status"] = "failed", "FAIL"
    report["elapsed_s"] = max(0.0, time.monotonic() - began)
    with (directory / "summary.json").open("x", encoding="utf-8", newline="\n") as output:
        json.dump(report, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps(_summary(report, directory), ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report["status"] in ("PASS", "PREFLIGHT_PASS") else 2


if __name__ == "__main__":
    sys.exit(main())
