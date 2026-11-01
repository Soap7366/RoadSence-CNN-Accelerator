"""Batch images using one validated, boot-loaded PL session and one Golden run."""
import argparse
import json
import math
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pscnn import __version__
from pscnn.batch import discover_images, run_image_batch
from pscnn.boot_state import load_boot_state
from pscnn.model import ModelSpec
from pscnn.overlay_artifact import validate_overlay
from pscnn.postprocess import decode_outputs
from tools.run_board import load_hardware_golden, _device_lock, _ip_mapping, _output_directory


def _emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def _summary(report, directory):
    batch = report.get("batch") or {}
    result = {
        "status": report["status"], "mode": report["mode"], "out_dir": str(directory),
        "hardware_accessed": report["hardware_accessed"],
        "inference_performed": report["inference_performed"],
        "overlay_downloaded": False,
        "used_boot_loaded_overlay": report["used_boot_loaded_overlay"],
        "hardware_initializations": report["hardware_initializations"],
        "self_test_runs": report["self_test_runs"],
        "golden_samples_passed": report["golden_samples_passed"],
        "images_discovered": report.get("images_discovered", 0),
        "images_processed": batch.get("processed", 0),
        "images_succeeded": batch.get("succeeded", 0),
        "images_failed": batch.get("failed", 0),
        "images_remaining": batch.get("remaining", report.get("images_discovered", 0)),
        "stopped_early": batch.get("stopped_early", False),
        "elapsed_s": report["elapsed_s"],
    }
    if report.get("error"):
        result["error"] = report["error"]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--recursive", action="store_true", help="Include image files in subdirectories")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--golden-manifest", required=True)
    parser.add_argument("--out-dir", required=True, help="New report directory inside this PS project")
    parser.add_argument("--timeout", type=float, default=15.0, help="Seconds per hardware frame")
    parser.add_argument("--execute", action="store_true", help="Run on this boot's PL; never download an Overlay")
    args = parser.parse_args(argv)
    try:
        directory = _output_directory(args.out_dir)
    except (OSError, ValueError) as exc:
        _emit({"status": "FAIL", "hardware_accessed": False, "error": str(exc)})
        return 2
    began = time.monotonic()
    report = {
        "schema": "pscnn.hardware-batch.v1", "framework_version": __version__,
        "status": "FAIL", "phase": "arguments",
        "mode": "hardware_batch" if args.execute else "batch_preflight",
        "reported_utc": datetime.now(timezone.utc).isoformat(), "board_clock_verified": False,
        "hardware_accessed": False, "inference_performed": False,
        "overlay_downloaded": False, "used_boot_loaded_overlay": False,
        "hardware_initializations": 0, "self_test_runs": 0,
        "golden_samples_passed": 0, "golden_results": [], "batch": None,
        "runtime": {"python": platform.python_version(), "machine": platform.machine()},
    }
    session = None
    try:
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError("Timeout must be finite and positive")
        report["timeout_s"] = args.timeout
        report["phase"] = "discover_images"
        input_path = Path(args.input_dir).expanduser()
        paths = discover_images(input_path, recursive=args.recursive)
        input_dir = input_path.resolve()
        report.update(input_dir=str(input_dir), recursive=args.recursive,
                      images_discovered=len(paths),
                      input_files=[str(path.relative_to(input_dir)) for path in paths])
        report["phase"] = "model"
        model = ModelSpec.load()
        report.update(model_id=model.model_id, quant_params_sha256=model.quant_sha256)
        report["phase"] = "validate_overlay"
        artifact = validate_overlay(args.manifest, model)
        report["overlay"] = artifact
        report["phase"] = "validate_all_golden_inputs"
        golden = load_hardware_golden(args.golden_manifest, model)
        report["golden_manifest"] = {key: golden[key] for key in
                                     ("manifest_path", "manifest_sha256", "sample_count")}
        report["golden_samples_validated"] = golden["sample_count"]
        if not args.execute:
            report["status"], report["phase"] = "PREFLIGHT_PASS", "preflight_complete"
        else:
            report["phase"] = "device_lock"
            with _device_lock():
                report["phase"] = "verify_boot_loaded_state"
                boot_state = load_boot_state(artifact)
                report["boot_state"] = boot_state
                report["phase"] = "initialize_hardware"
                import pynq
                from pscnn.axi_transport import AxiLiteTransport
                from pscnn.control import ControlSession
                report["runtime"]["pynq"] = getattr(pynq, "__version__", "unknown")
                overlay = pynq.Overlay(artifact["bit_path"], download=False)
                base, size = _ip_mapping(overlay, artifact)
                again = validate_overlay(args.manifest, model)
                for key in ("manifest_sha256", "bit_sha256", "hwh_sha256", "phys_addr", "addr_range"):
                    if again[key] != artifact[key]:
                        raise ValueError("Overlay artifact changed during batch preflight: " + key)
                if load_boot_state(artifact) != boot_state:
                    raise ValueError("Boot state changed during batch preflight")
                report["used_boot_loaded_overlay"] = True
                report.update(phys_addr=base, addr_range=size)
                report["hardware_accessed"] = True
                registers = pynq.MMIO(base, size)
                transport = AxiLiteTransport(registers, model)
                identity = transport.describe(args.timeout)
                report["device_identity"] = {
                    "protocol": identity.protocol, "model_id": identity.model_id,
                    "quant_params_sha256": identity.quant_params_sha256,
                    "input_bytes": identity.input_bytes,
                }
                session = ControlSession(transport, model)
                report["hardware_initializations"] = 1
                report["self_test_runs"] = 1
                report["phase"] = "golden_self_test"
                for sample in golden["samples"]:
                    raw = session.infer(sample["payload"], timeout_s=args.timeout)
                    report["inference_performed"] = True
                    expected = {key: sample[key] for key in ("collision_int32", "steering_int32")}
                    passed = raw == expected
                    report["golden_results"].append({
                        "id": sample["id"], "input_sha256": sample["input_sha256"],
                        "raw": raw, "expected_raw": expected, "passed": passed,
                        "decoded": decode_outputs(raw["collision_int32"], raw["steering_int32"], model),
                        "control_run": dict(session.last_run),
                    })
                    if not passed:
                        raise RuntimeError("Hardware raw INT32 differs from Golden sample " + str(sample["id"]))
                    report["golden_samples_passed"] += 1
                _emit({"event": "self_test_complete", "status": "PASS",
                       "golden_samples_passed": report["golden_samples_passed"], "self_test_runs": 1})
                report["phase"] = "image_batch"
                report["batch"] = run_image_batch(
                    paths, input_dir, directory / "images", model, session,
                    timeout_s=args.timeout,
                    emit=lambda item: _emit({"event": "image_result", **item}))
                report["status"] = report["batch"]["status"]
                report["phase"] = "complete"
    except (Exception, KeyboardInterrupt) as exc:
        report["failed_phase"] = report["phase"]
        report["error"] = {"type": type(exc).__name__, "message": str(exc), "phase": report["phase"]}
        if session is not None:
            report["failed_control_run"] = dict(session.last_run)
            report["inference_performed"] = bool(
                report["inference_performed"] or session.last_run.get("start_accepted"))
        report["status"], report["phase"] = "FAIL", "failed"
    report["elapsed_s"] = max(0.0, time.monotonic() - began)
    temporary = directory / "summary.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                         encoding="utf-8")
    temporary.replace(directory / "summary.json")
    print(json.dumps(_summary(report, directory), ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    return 0 if report["status"] in ("PASS", "PREFLIGHT_PASS") else 2


if __name__ == "__main__":
    sys.exit(main())
