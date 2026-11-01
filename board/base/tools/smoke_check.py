"""Small PS software check: no bitstream download and no PL access."""
import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="New directory inside this project")
    parser.add_argument("--image", default="examples/synthetic_rgb.png", help="Image to prepare and verify")
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir = (out_dir if out_dir.is_absolute() else ROOT / out_dir).resolve()
    if ROOT not in out_dir.parents:
        raise ValueError("Keep smoke-check outputs inside this project")
    out_dir.mkdir(parents=True, exist_ok=False)

    cli_count = 0

    def execute(name, arguments, expected_exit=0):
        nonlocal cli_count
        process = subprocess.run([sys.executable, "-B", "-X", "utf8"] + arguments, cwd=str(ROOT),
                                 text=True, encoding="utf-8", capture_output=True)
        (out_dir / (name + ".stdout.txt")).write_text(process.stdout, encoding="utf-8")
        (out_dir / (name + ".stderr.txt")).write_text(process.stderr, encoding="utf-8")
        if process.returncode != expected_exit:
            raise RuntimeError("{} exited {}; see {}".format(name, process.returncode, out_dir))
        if arguments[0] == "app.py":
            cli_count += 1
        return process

    tests = execute("unittest", ["-m", "unittest", "discover", "-s", "tests", "-v"])
    count_match = re.search(r"Ran (\d+) tests?", tests.stderr)
    if not count_match or int(count_match.group(1)) < 10 or "\nOK\n" not in tests.stderr:
        raise RuntimeError("Offline unit tests did not complete")
    unit_count = int(count_match.group(1))
    environment = json.loads(execute("environment", ["app.py", "check-env"]).stdout)
    source = Path(args.image)
    source = source if source.is_absolute() else ROOT / source
    prepared = json.loads(execute("prepare", [
        "app.py", "prepare", str(source),
        "--out-dir", str(out_dir / "prepared")]).stdout)
    verified_input = json.loads(execute("verify_input", [
        "app.py", "verify-input", str(out_dir / "prepared"),
        "--source-image", str(source)]).stdout)
    diagnosis = json.loads(execute("doctor", [
        "app.py", "doctor", "--prepared-dir", str(out_dir / "prepared"),
        "--source-image", str(source)]).stdout)
    if (not diagnosis["software_ready"] or diagnosis["input_ready"] is not True or
            diagnosis["hardware_accessed"] or diagnosis["inference_performed"] or
            diagnosis["pl_integration_ready"]):
        raise RuntimeError("Read-only software/input diagnostics failed or claimed PL access")
    control = json.loads(execute("control_check", [
        "app.py", "control-check", "--prepared-dir", str(out_dir / "prepared"),
        "--source-image", str(source)]).stdout)
    if (control["mode"] != "software_transport_simulation" or control["hardware_accessed"] or
            control["inference_performed"] or control["cnn_model_computed"] or control["prediction_available"]):
        raise RuntimeError("Control simulation must not claim hardware access or CNN inference")
    if control["input_int8_sha256"] != control["simulated_memory_sha256"]:
        raise RuntimeError("Simulated input memory differs from the prepared frame")
    if control["diagnostic_signed_values"] != {"word0": -1, "word1": -(1 << 31)}:
        raise RuntimeError("Diagnostic unsigned-to-signed INT32 conversion is incorrect")
    if any(name in control for name in ("risk_score", "steering_value", "collision_logit")):
        raise RuntimeError("Control sentinel values must not be decoded as image predictions")
    for prefix in ("000_collision_validation", "029_steering_validation"):
        result = json.loads(execute("replay_" + prefix, [
            "app.py", "run", "--backend", "replay",
            "--input-bin", "fixtures/" + prefix + "/input_int8.bin",
            "--fixture", "fixtures/" + prefix + "/expected.json"]).stdout)
        expected = json.loads((ROOT / "fixtures" / prefix / "expected.json").read_text(encoding="utf-8"))
        if result["mode"] != "golden_replay" or result["inference_performed"] or result["hardware_verified"]:
            raise RuntimeError("Replay must never claim CNN inference or hardware verification")
        for head in ("collision", "steering"):
            if result[head + "_int32"] != expected[head + "_int32"]:
                raise RuntimeError("Wrong replay result for " + prefix)
    placeholder = execute("hardware_placeholder", [
        "app.py", "run", "--backend", "pynq",
        "--prepared-dir", str(out_dir / "prepared")], expected_exit=2)
    if "not implemented" not in placeholder.stderr:
        raise RuntimeError("Hardware placeholder must explicitly fail")
    fixture = json.loads((ROOT / "fixtures/000_collision_validation/expected.json").read_text(encoding="utf-8"))
    replay_misuse_rejected = None
    if prepared["input_int8_sha256"] != fixture["input_int8_sha256"]:
        refused = execute("reject_unmatched_replay", [
            "app.py", "run", "--backend", "replay", "--prepared-dir", str(out_dir / "prepared"),
            "--fixture", "fixtures/000_collision_validation/expected.json"], expected_exit=2)
        if "SHA256" not in refused.stderr:
            raise RuntimeError("Unmatched image must not receive a Golden prediction")
        replay_misuse_rejected = True
    manifest_path = ROOT / "deployment_manifest.json"
    verified = 0
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for name, entry in manifest["files"].items():
            path = (ROOT / name).resolve()
            if ROOT not in path.parents:
                raise ValueError("Invalid deployment manifest path")
            data = path.read_bytes()
            if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise RuntimeError("Deployed file differs from local package: " + name)
            verified += 1
    summary = {
        "status": "PASS", "board_reported_utc": datetime.now(timezone.utc).isoformat(),
        "board_clock_verified": False, "python": platform.python_version(),
        "machine": platform.machine(), "environment": environment,
        "unit_tests_passed": unit_count, "cli_checks_passed": cli_count, "golden_replay_samples": 2,
        "deployment_files_sha256_verified": verified,
        "source_image": str(source.resolve()),
        "source_image_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "control_simulation_passed": control["status"] == "PASS",
        "control_simulated_memory_sha256": control["simulated_memory_sha256"],
        "doctor_software_ready": diagnosis["software_ready"],
        "doctor_input_ready": diagnosis["input_ready"],
        "doctor_status": diagnosis["status"],
        "input_bundle_verified": verified_input["input_payload_valid"],
        "source_image_verified": verified_input["source_image_verified"],
        "unmatched_replay_rejected": replay_misuse_rejected,
        "prepared_input_bytes": prepared["bytes"],
        "prepared_input_sha256": prepared["input_int8_sha256"],
        "pynq_backend_implemented": False, "pl_accessed": False, "cnn_inference_executed": False,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
