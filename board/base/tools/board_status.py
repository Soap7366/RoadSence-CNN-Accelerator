"""Read the current boot's already-loaded PL identity/status; never program or reset."""
import argparse
import json
import math
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pscnn.model import ModelSpec
from pscnn.overlay_artifact import validate_overlay
from pscnn.boot_state import STATE_PATH, load_boot_state
from tools.run_board import _device_lock, _ip_mapping


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--state", default=STATE_PATH)
    args = parser.parse_args(argv)
    report = {"status": "FAIL", "phase": "arguments", "state_path": args.state,
              "hardware_accessed": False, "overlay_downloaded": False, "inference_performed": False,
              "used_boot_loaded_overlay": False}
    try:
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError("Timeout must be finite and positive")
        deadline = time.monotonic() + args.timeout

        def remaining():
            value = deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError("PL status deadline exceeded")
            return value

        report["phase"] = "validate_overlay"
        model = ModelSpec.load()
        artifact = validate_overlay(args.manifest, model)
        report["phase"] = "device_lock"
        with _device_lock():
            report["phase"] = "verify_boot_loaded_state"
            marker = load_boot_state(artifact, state_path=args.state)
            report["phase"] = "parse_pynq_hwh"
            import pynq
            from pscnn.axi_transport import AxiLiteTransport, REG_RESET_SEQ, REG_START_SEQ
            overlay = pynq.Overlay(artifact["bit_path"], download=False)
            base, size = _ip_mapping(overlay, artifact)
            remaining()
            again = validate_overlay(args.manifest, model)
            for name in ("manifest_sha256", "bit_sha256", "hwh_sha256", "phys_addr", "addr_range"):
                if artifact[name] != again[name]:
                    raise ValueError("Artifact changed while inspecting PL: " + name)
            if marker != load_boot_state(artifact, state_path=args.state):
                raise ValueError("Boot evidence changed while inspecting PL")
            report["phase"] = "read_pl_identity_status"
            report["hardware_accessed"] = True
            registers = pynq.MMIO(base, size)
            transport = AxiLiteTransport(registers, model)
            identity = transport.describe(remaining())
            state = transport.read_status(remaining())
            if state.error or state.busy or not (state.host_ready or state.result_valid):
                raise RuntimeError("PL is not idle with a ready input or held result")
            report["boot_id"] = marker["boot_id"]
            report["phys_addr"], report["addr_range"] = base, size
            report["device_identity"] = {
                "protocol": identity.protocol, "model_id": identity.model_id,
                "quant_params_sha256": identity.quant_params_sha256, "input_bytes": identity.input_bytes}
            report["pl_status"] = asdict(state)
            report["reset_seq"] = int(registers.read(REG_RESET_SEQ))
            report["start_seq"] = int(registers.read(REG_START_SEQ))
            remaining()
            report["used_boot_loaded_overlay"] = True
        report["phase"], report["status"] = "complete", "PL_READY"
    except Exception as exc:
        report["failed_phase"] = report["phase"]
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        report["phase"] = "failed"
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    return 0 if report["status"] == "PL_READY" else 2


if __name__ == "__main__":
    sys.exit(main())
