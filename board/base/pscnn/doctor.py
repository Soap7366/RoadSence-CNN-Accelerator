"""Read-only PS diagnostics. No Overlay, MMIO or FPGA device access."""
import hashlib
import importlib
import json
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path, PurePosixPath, PureWindowsPath

from .model import ModelSpec, PROJECT_ROOT


def _runtime_info():
    packages = {}
    ready = sys.version_info >= (3, 8)
    for name, module_name in (("numpy", "numpy"), ("Pillow", "PIL")):
        try:
            module = importlib.import_module(module_name)
            packages[name] = {"available": True, "version": str(module.__version__)}
        except Exception as exc:
            packages[name] = {"available": False, "version": None, "error": str(exc)}
            ready = False
    # Distribution metadata does not import PYNQ or initialize FPGA devices.
    try:
        pynq_version = metadata.version("pynq")
    except metadata.PackageNotFoundError:
        pynq_version = None
    except Exception as exc:
        packages["pynq"] = {"available": None, "version": None, "error": str(exc)}
    if "pynq" not in packages:
        packages["pynq"] = {"available": pynq_version is not None, "version": pynq_version}
    return {"status": "PASS" if ready else "FAIL", "python": platform.python_version(),
            "executable": sys.executable, "machine": platform.machine(),
            "system": platform.system(), "packages": packages,
            "pynq_version_source": "installed distribution metadata; PYNQ not imported"}


def _system_info():
    result = {"status": "UNKNOWN", "system": platform.system(),
              "kernel": platform.release(), "hostname": platform.node(),
              "device_tree_model": None, "os_release": None}
    if result["system"] != "Linux":
        result["reason"] = "Linux board identity is unavailable on this operating system"
        return result
    for path in (Path("/proc/device-tree/model"), Path("/sys/firmware/devicetree/base/model")):
        try:
            result["device_tree_model"] = path.read_bytes().decode("utf-8", errors="replace").rstrip("\x00\r\n")
            break
        except OSError:
            continue
    try:
        values = {}
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip().strip("\"'")
        result["os_release"] = values
        result["status"] = "PASS"
    except (OSError, UnicodeError) as exc:
        result["reason"] = "Cannot read /etc/os-release: " + str(exc)
    return result


def _network_info():
    result = {"status": "UNKNOWN", "addresses": None, "routes": None, "commands": {}}
    if platform.system() != "Linux":
        result["reason"] = "Linux ip diagnostics are unavailable on this operating system"
        return result
    executable = shutil.which("ip")
    if not executable:
        result["reason"] = "ip command is not installed or is not on PATH"
        return result
    for name, arguments in (("addresses", ["-j", "addr", "show"]),
                            ("routes", ["-j", "route", "show"])):
        argv = [executable] + arguments
        try:
            process = subprocess.run(argv, shell=False, check=False, capture_output=True,
                                     text=True, encoding="utf-8", errors="replace", timeout=3)
            detail = {"argv": argv, "returncode": process.returncode,
                      "stderr": process.stderr.strip(), "status": "UNKNOWN"}
            result["commands"][name] = detail
            if process.returncode != 0:
                detail["reason"] = "ip command returned a nonzero exit code"
                continue
            data = json.loads(process.stdout)
            if not isinstance(data, list):
                raise ValueError("ip JSON output must be an array")
            result[name] = data
            detail["status"] = "PASS"
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            result["commands"][name] = {"argv": argv, "status": "UNKNOWN", "reason": str(exc)}
    if all(result[name] is not None for name in ("addresses", "routes")):
        result["status"] = "PASS"
    else:
        result["reason"] = "Some network information is unavailable; no network settings were changed"
    return result


def _deployment_info(root):
    manifest_path = root / "deployment_manifest.json"
    if not manifest_path.exists():
        return {"status": "UNKNOWN", "manifest": str(manifest_path),
                "verified_files": 0, "reason": "No deployment manifest is present"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict) or not manifest["files"]:
        raise ValueError("Deployment manifest must contain a nonempty files object")
    verified = 0
    for name, entry in manifest["files"].items():
        if not isinstance(name, str) or not name:
            raise ValueError("Invalid deployment manifest path")
        posix = PurePosixPath(name.replace("\\", "/"))
        windows = PureWindowsPath(name)
        if posix.is_absolute() or windows.drive or ".." in posix.parts:
            raise ValueError("Deployment manifest path must stay inside project: " + name)
        path = (root / Path(*posix.parts)).resolve()
        if root not in path.parents:
            raise ValueError("Deployment manifest path must stay inside project: " + name)
        if not isinstance(entry, dict) or type(entry.get("bytes")) is not int or entry["bytes"] < 0:
            raise ValueError("Invalid file size in deployment manifest: " + name)
        expected_hash = entry.get("sha256")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash):
            raise ValueError("Invalid SHA256 in deployment manifest: " + name)
        data = path.read_bytes()
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != expected_hash.lower():
            raise ValueError("Deployed file size/SHA256 mismatch: " + name)
        verified += 1
    return {"status": "PASS", "manifest": str(manifest_path), "verified_files": verified,
            "note": "Only files listed in this manifest were verified"}


def _project_path(value, root):
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def _overlay_info(directory):
    if not directory.is_dir():
        raise ValueError("Overlay directory does not exist: " + str(directory))
    files = {path.name: path for path in directory.iterdir() if path.is_file()}
    bits = {path.stem: path.name for path in files.values() if path.suffix.lower() == ".bit"}
    hwhs = {path.stem: path.name for path in files.values() if path.suffix.lower() == ".hwh"}
    names = sorted(set(bits) & set(hwhs))
    return {"status": "PASS" if names and set(bits) == set(hwhs) else "UNKNOWN",
            "directory": str(directory),
            "pairs": [{"stem": name, "bit": bits[name], "hwh": hwhs[name]} for name in names],
            "unpaired_bit": [bits[name] for name in sorted(set(bits) - set(hwhs))],
            "unpaired_hwh": [hwhs[name] for name in sorted(set(hwhs) - set(bits))],
            "files_only": True, "hardware_validated": False,
            "note": "Matching filenames do not prove matching builds, interfaces or model identity"}


def collect_diagnostics(project_root=None, prepared_dir=None, source_image=None, overlay_dir=None):
    """Collect portable software checks and optional input/file checks without hardware access.

    Relative optional paths are resolved against project_root. software_ready covers
    Python, preprocessing packages, model contract and any present deployment manifest;
    PYNQ availability is reported separately, and does not prevent PC preprocessing.
    """
    root = Path(project_root).resolve() if project_root is not None else PROJECT_ROOT.resolve()
    report = {"schema": "pscnn.diagnostics.v1", "collected_utc": datetime.now(timezone.utc).isoformat(),
              "project_root": str(root), "software_ready": False, "input_ready": None,
              "pl_integration_ready": False, "board_clock_verified": False,
              "hardware_accessed": False, "inference_performed": False,
              "checks": {}, "errors": [], "warnings": []}
    checks = report["checks"]
    checks["runtime"] = _runtime_info()
    if checks["runtime"]["status"] != "PASS":
        report["errors"].append("runtime: Python 3.8+ and importable NumPy/Pillow are required")
    pynq_available = checks["runtime"]["packages"].get("pynq", {}).get("available")
    if pynq_available is not True:
        report["warnings"].append("runtime: PYNQ distribution is unavailable or unknown; PC image preparation can still work")
    checks["system"] = _system_info()
    checks["network"] = _network_info()
    for name in ("system", "network"):
        if checks[name]["status"] == "UNKNOWN":
            report["warnings"].append(name + ": " + checks[name].get("reason", "Information is unavailable"))
    model = None
    try:
        model = ModelSpec.load(root / "config")
        checks["model"] = {"status": "PASS", "model_id": model.model_id,
                           "quant_params_sha256": model.quant_sha256}
    except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError) as exc:
        checks["model"] = {"status": "FAIL", "error": str(exc)}
        report["errors"].append("model: " + str(exc))
    try:
        checks["deployment"] = _deployment_info(root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        checks["deployment"] = {"status": "FAIL", "error": str(exc), "verified_files": 0}
        report["errors"].append("deployment: " + str(exc))
    if checks["deployment"]["status"] == "UNKNOWN":
        report["warnings"].append("deployment: " + checks["deployment"]["reason"])
    if prepared_dir is None:
        checks["input"] = {"status": "NOT_REQUESTED", "reason": "No prepared input directory was supplied"}
        if source_image is not None:
            report["warnings"].append("input: source_image was ignored because no prepared_dir was supplied")
    elif model is None:
        checks["input"] = {"status": "FAIL", "error": "Cannot verify input without a valid model contract"}
        report["input_ready"] = False
        report["errors"].append("input: Cannot verify input without a valid model contract")
    else:
        try:
            from .bundle import load_prepared
            prepared = _project_path(prepared_dir, root)
            source = _project_path(source_image, root) if source_image is not None else None
            _, verified = load_prepared(prepared, model, source)
            checks["input"] = {"status": "PASS", **verified}
            report["input_ready"] = True
        except (ImportError, OSError, ValueError, KeyError, TypeError) as exc:
            checks["input"] = {"status": "FAIL", "error": str(exc)}
            report["input_ready"] = False
            report["errors"].append("input: " + str(exc))
    if overlay_dir is None:
        checks["overlay_files"] = {"status": "NOT_REQUESTED", "pairs": [], "hardware_validated": False}
    else:
        try:
            checks["overlay_files"] = _overlay_info(_project_path(overlay_dir, root))
            if checks["overlay_files"]["status"] != "PASS":
                report["warnings"].append("overlay_files: No complete set of matching .bit/.hwh filenames was found")
        except (OSError, ValueError) as exc:
            checks["overlay_files"] = {"status": "UNKNOWN", "error": str(exc), "hardware_validated": False}
            report["warnings"].append("overlay_files: " + str(exc))
    report["software_ready"] = (checks["runtime"]["status"] == "PASS" and
                                checks["model"]["status"] == "PASS" and
                                checks["deployment"]["status"] != "FAIL")
    report["status"] = "FAIL" if report["errors"] else ("PASS_WITH_WARNINGS" if report["warnings"] else "PASS")
    report["limitations"] = ["Network details are observations, not proof of external connectivity",
                             "The board clock has not been verified",
                             "This read-only command does not verify the active PL/bitstream/model; see a separate hardware-run report"]
    return report
