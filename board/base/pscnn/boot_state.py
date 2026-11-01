"""Same-boot proof for the PL image loaded by the boot service; no PYNQ import."""
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

STATE_PATH = "/run/pscnn/boot_state.json"
SCHEMA = "pscnn.boot-state.v1"
BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"
FPGA_STATE_PATH = "/sys/class/fpga_manager/fpga0/state"
PROTOCOL = "pscnn.host.draft.v1"


def current_boot_id():
    return _boot_id(Path(BOOT_ID_PATH).read_text(encoding="ascii").strip())


def _boot_id(value):
    if not isinstance(value, str):
        raise ValueError("Boot id must be a UUID string")
    try:
        parsed = str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Invalid boot id") from exc
    if value != parsed:
        raise ValueError("Boot id must use canonical UUID spelling")
    return value


def fpga_manager_state():
    return Path(FPGA_STATE_PATH).read_text(encoding="ascii").strip()


def _counter(value, name):
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(name + " must be an unsigned 32-bit integer")


def _validate(record, artifact, boot_id):
    if not isinstance(record, dict):
        raise ValueError("Boot state must be a JSON object")
    expected = {"schema": SCHEMA, "status": "PASS", "boot_id": _boot_id(boot_id),
                "manifest_sha256": artifact["manifest_sha256"], "bit_sha256": artifact["bit_sha256"],
                "hwh_sha256": artifact["hwh_sha256"], "phys_addr": artifact["phys_addr"],
                "addr_range": artifact["addr_range"], "ip_name": artifact["ip_name"]}
    for name, value in expected.items():
        if record.get(name) != value or type(record.get(name)) is not type(value):
            raise ValueError("Boot state mismatch: " + name)
    for name in ("manifest_sha256", "bit_sha256", "hwh_sha256"):
        value = record[name]
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("Boot state has an invalid SHA256: " + name)
    identity = record.get("identity")
    if not isinstance(identity, dict):
        raise ValueError("Boot state is missing its hardware identity")
    for name, value in (("protocol", PROTOCOL), ("model_id", artifact["model_id"]),
                        ("quant_params_sha256", artifact["quant_params_sha256"]), ("input_bytes", 4096)):
        if identity.get(name) != value or type(identity.get(name)) is not type(value):
            raise ValueError("Boot hardware identity mismatch: " + name)
    for name in ("reset_seq", "start_seq"):
        _counter(record.get(name), name)
    if record.get("board_clock_verified") is not False:
        raise ValueError("Boot state must not claim a verified wall clock")
    if record.get("hardware_accessed") is not True or record.get("overlay_downloaded") is not True:
        raise ValueError("Boot state does not record a completed hardware download")
    if not isinstance(record.get("reported_utc"), str) or not record["reported_utc"]:
        raise ValueError("Boot state is missing its report time")
    return record


def load_boot_state(artifact, state_path=STATE_PATH, boot_id=None, fpga_state=None):
    """Refuse missing/stale boot evidence before callers map any PL registers."""
    current = current_boot_id() if boot_id is None else _boot_id(boot_id)
    actual_state = fpga_manager_state() if fpga_state is None else fpga_state
    if actual_state != "operating":
        raise ValueError("FPGA manager is not operating")
    try:
        record = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("No successful PL boot state; check the boot service") from exc
    return _validate(record, artifact, current)


def clear_boot_state(state_path=STATE_PATH):
    try:
        Path(state_path).unlink()
    except FileNotFoundError:
        pass


def write_boot_state(artifact, identity, reset_seq, start_seq, state_path=STATE_PATH, boot_id=None):
    """Publish only a complete PASS record; callers must own the global device lock."""
    record = {
        "schema": SCHEMA, "status": "PASS",
        "boot_id": current_boot_id() if boot_id is None else _boot_id(boot_id),
        "manifest_sha256": artifact["manifest_sha256"], "bit_sha256": artifact["bit_sha256"],
        "hwh_sha256": artifact["hwh_sha256"], "phys_addr": artifact["phys_addr"],
        "addr_range": artifact["addr_range"], "ip_name": artifact["ip_name"], "identity": dict(identity),
        "reset_seq": reset_seq, "start_seq": start_seq,
        "reported_utc": datetime.now(timezone.utc).isoformat(), "board_clock_verified": False,
        "hardware_accessed": True, "overlay_downloaded": True,
    }
    _validate(record, artifact, record["boot_id"])
    target = Path(state_path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    descriptor, temporary = tempfile.mkstemp(prefix=".boot-state-", dir=str(target.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            json.dump(record, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return record
