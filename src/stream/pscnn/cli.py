import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from . import __version__
from .model import ModelSpec, PROJECT_ROOT, sha256
from .postprocess import decode_outputs


def save_prepared(*args, **kwargs):
    # Keep the public helper while allowing doctor to run without image packages.
    from .bundle import save_prepared as prepare
    return prepare(*args, **kwargs)


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Pinned dual-task CNN PS starter; hardware integration pending.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-env", help="Report the current Python environment; no board is probed")
    prepare = commands.add_parser("prepare", help="Original image -> 64x64 grayscale and 4096-byte INT8")
    prepare.add_argument("image")
    prepare.add_argument("--out-dir", help="New directory only; defaults to project outputs/<UTC timestamp>")
    decode = commands.add_parser("decode", help="Decode two signed INT32 values; no inference")
    decode.add_argument("--collision-int32", required=True, type=int)
    decode.add_argument("--steering-int32", required=True, type=int)
    decode.add_argument("--threshold", type=float, default=None)
    run = commands.add_parser("run", help="Replay a known Golden or select the explicit hardware placeholder")
    run.add_argument("--backend", choices=("replay", "pynq"), required=True)
    input_group = run.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--input-bin")
    input_group.add_argument("--prepared-dir", help="Validated input bundle from prepare")
    run.add_argument("--fixture", help="Required for replay: fixtures/<prefix>/expected.json")
    run.add_argument("--timeout", type=float, default=1.0)
    run.add_argument("--threshold", type=float, default=None)
    verify = commands.add_parser("verify-input", help="Verify BIN/HEX/gray/model before future PL upload")
    verify.add_argument("directory")
    verify.add_argument("--source-image")
    doctor = commands.add_parser("doctor", help="Read-only software/network/input diagnostics; no PL access")
    doctor.add_argument("--prepared-dir")
    doctor.add_argument("--source-image")
    doctor.add_argument("--overlay-dir", help="Only inventory matching .bit/.hwh filenames; no loading")
    control = commands.add_parser("control-check", help="RAM-only PS control simulation; no CNN or PL")
    control_input = control.add_mutually_exclusive_group(required=True)
    control_input.add_argument("--prepared-dir")
    control_input.add_argument("--input-bin")
    control.add_argument("--source-image", help="Recheck source image with --prepared-dir")
    control.add_argument("--timeout", type=float, default=1.0)
    args = parser.parse_args(argv)
    if args.command == "doctor":
        if args.source_image and not args.prepared_dir:
            parser.error("--source-image requires --prepared-dir")
        from .doctor import collect_diagnostics
        report = collect_diagnostics(prepared_dir=args.prepared_dir, source_image=args.source_image,
                                     overlay_dir=args.overlay_dir)
        report["framework_version"] = __version__
        print_json(report)
        return 2 if report["errors"] else 0
    model = ModelSpec.load()
    if args.command == "check-env":
        import numpy as np
        import PIL
        try:
            import pynq
            pynq_version = getattr(pynq, "__version__", "unknown")
        except ImportError:
            pynq_version = None
        print_json({"framework_version": __version__, "python": sys.version, "executable": sys.executable,
                    "machine": platform.machine(), "numpy": np.__version__, "Pillow": PIL.__version__,
                    "pynq": pynq_version, "model_id": model.model_id,
                    "quant_params_sha256": model.quant_sha256, "hardware_backend_implemented": False,
                    "axi_transport_implemented": True, "hardware_runner": "tools/run_board.py",
                    "hardware_runner_requires_verified_artifacts": True})
    elif args.command == "prepare":
        out_dir = args.out_dir or PROJECT_ROOT / "outputs" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        print_json(save_prepared(args.image, out_dir, model))
    elif args.command == "verify-input":
        from .bundle import load_prepared
        _, report = load_prepared(args.directory, model, args.source_image)
        print_json(report)
    elif args.command == "decode":
        print_json({"mode": "decode_only", "inference_performed": False,
                    "model_id": model.model_id,
                    **decode_outputs(args.collision_int32, args.steering_int32, model, args.threshold)})
    elif args.command == "control-check":
        from .bundle import load_prepared
        from .preprocess import validate_input
        from .control import ControlSession
        from .sim_transport import MemoryTransport
        if args.source_image and not args.prepared_dir:
            parser.error("--source-image requires --prepared-dir")
        if args.prepared_dir:
            payload, verified = load_prepared(args.prepared_dir, model, args.source_image)
        else:
            payload = validate_input(Path(args.input_bin).read_bytes())
            verified = None
        transport = MemoryTransport(model)
        session = ControlSession(transport, model)
        diagnostic = session.infer(payload, timeout_s=args.timeout)
        if bytes(transport.memory) != payload or transport.starts != 1 or transport.resets != 1:
            raise RuntimeError("RAM-only control check did not load and start exactly one complete frame")
        print_json({
            "schema": "pscnn.control-check.v1", "status": "PASS",
            "mode": "software_transport_simulation", "framework_version": __version__,
            "model_id": model.model_id, "quant_params_sha256": model.quant_sha256,
            "input_int8_sha256": sha256(payload), "input_bundle_verified": verified is not None,
            "source_image_verified": bool(verified and verified["source_image_verified"]),
            "logical_address_first": 0, "logical_address_last": 4095,
            "simulated_memory_sha256": sha256(bytes(transport.memory)),
            "diagnostic_result_source": "Fixed software sentinel words; unrelated to the image; not CNN outputs",
            "diagnostic_unsigned_words": list(transport.diagnostic_words),
            "diagnostic_signed_values": {
                "word0": diagnostic["collision_int32"], "word1": diagnostic["steering_int32"]},
            "control_run": session.last_run, "transport_events": transport.events,
            "hardware_accessed": False, "pl_written": False,
            "inference_performed": False, "cnn_model_computed": False, "prediction_available": False,
        })
    elif args.command == "run":
        from .backend import GoldenReplayBackend, PynqBackend
        from .bundle import load_prepared
        from .preprocess import validate_input
        if args.backend == "replay" and not args.fixture:
            parser.error("--fixture is required for the replay backend")
        if args.prepared_dir:
            payload, _ = load_prepared(args.prepared_dir, model)
        else:
            payload = validate_input(Path(args.input_bin).read_bytes())
        backend = GoldenReplayBackend(args.fixture, model) if args.backend == "replay" else PynqBackend(model)
        raw = backend.infer(payload, args.timeout)
        print_json({"mode": "golden_replay", "inference_performed": False, "hardware_verified": False,
                    "model_id": model.model_id, "quant_params_sha256": model.quant_sha256,
                    "input_int8_sha256": sha256(payload),
                    **decode_outputs(raw["collision_int32"], raw["steering_int32"], model, args.threshold)})
