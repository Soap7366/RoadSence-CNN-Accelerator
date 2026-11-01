"""Boot-service entry: verify, load and reset PL; never run an image."""
import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pscnn.model import ModelSpec
from pscnn.overlay_artifact import validate_overlay
from pscnn.boot_state import (STATE_PATH, clear_boot_state, write_boot_state, fpga_manager_state)
from tools.run_board import _device_lock, _ip_mapping


def wait_for_hardware(deadline, clock=time.monotonic, sleep=time.sleep):
    required = (Path("/sys/class/fpga_manager/fpga0/state"), Path("/dev/dri/renderD128"))
    while not all(path.exists() for path in required):
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError("FPGA manager/XRT render device was not ready before timeout")
        sleep(min(0.2, remaining))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--state", default=STATE_PATH)
    args = parser.parse_args(argv)
    report = {"status": "FAIL", "phase": "arguments", "state_path": args.state,
              "hardware_accessed": False, "overlay_downloaded": False, "inference_performed": False}
    try:
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError("Timeout must be finite and positive")
        deadline = time.monotonic() + args.timeout

        def remaining():
            value = deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError("PL boot initialization exceeded its deadline")
            return value

        report["phase"] = "device_lock"
        with _device_lock():
            try:
                clear_boot_state(args.state)
                report["phase"] = "validate_overlay"
                model = ModelSpec.load()
                artifact = validate_overlay(args.manifest, model)
                report["phase"] = "wait_for_hardware"
                wait_for_hardware(deadline)
                report["phase"] = "parse_pynq_hwh"
                import pynq
                from pscnn.axi_transport import AxiLiteTransport, REG_RESET_SEQ, REG_START_SEQ, REG_INPUT_COUNT
                remaining()
                overlay = pynq.Overlay(artifact["bit_path"], download=False)
                base, size = _ip_mapping(overlay, artifact)
                remaining()
                report["phase"] = "revalidate_before_download"
                again = validate_overlay(args.manifest, model)
                for name in ("manifest_sha256", "bit_sha256", "hwh_sha256", "phys_addr", "addr_range"):
                    if artifact[name] != again[name]:
                        raise ValueError("Overlay artifact changed during boot: " + name)
                report["phase"] = "download_overlay"
                report["hardware_accessed"] = True
                remaining()
                overlay.download()
                report["overlay_downloaded"] = True
                remaining()
                report["phase"] = "verify_device_identity"
                registers = pynq.MMIO(base, size)
                transport = AxiLiteTransport(registers, model)
                identity = transport.describe(remaining())
                report["phase"] = "reset_core"
                transport.reset(remaining())
                status = transport.read_status(remaining())
                if status.busy or not status.host_ready or status.result_valid or status.error:
                    raise RuntimeError("PL did not enter idle/ready/valid-clear state")
                reset_seq, start_seq = int(registers.read(REG_RESET_SEQ)), int(registers.read(REG_START_SEQ))
                if int(registers.read(REG_INPUT_COUNT)) != 0:
                    raise RuntimeError("PL input counter was not cleared")
                remaining()
                if fpga_manager_state() != "operating":
                    raise RuntimeError("FPGA manager is not operating after download")
                report["phase"] = "publish_boot_state"
                record = write_boot_state(artifact, {
                    "protocol": identity.protocol, "model_id": identity.model_id,
                    "quant_params_sha256": identity.quant_params_sha256, "input_bytes": identity.input_bytes},
                    reset_seq, start_seq, state_path=args.state)
                report["boot_id"] = record["boot_id"]
                report["device_identity"] = record["identity"]
                report["phys_addr"], report["addr_range"] = base, size
            except Exception:
                # Never remove another owner's evidence, and clean failures before unlock.
                try:
                    clear_boot_state(args.state)
                except OSError as cleanup:
                    report["state_cleanup_error"] = str(cleanup)
                raise
        report["phase"], report["status"] = "complete", "PL_BOOT_READY"
    except Exception as exc:
        report["failed_phase"] = report["phase"]
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        report["phase"] = "failed"
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    return 0 if report["status"] == "PL_BOOT_READY" else 2


if __name__ == "__main__":
    sys.exit(main())
