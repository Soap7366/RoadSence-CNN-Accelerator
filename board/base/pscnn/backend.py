"""Backend boundary. Replay is a fixture check, not a CNN implementation."""
import json
import math
from abc import ABC, abstractmethod
from pathlib import Path
from .model import sha256
from .preprocess import validate_input
from .postprocess import checked_int32


class Backend(ABC):
    @abstractmethod
    def infer(self, payload, timeout_s=1.0):
        """Future hardware: load completely, start, wait, check error/valid, read both."""
        raise NotImplementedError


class GoldenReplayBackend(Backend):
    def __init__(self, fixture_path, model):
        self.fixture_path = Path(fixture_path)
        self.fixture = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        if self.fixture["model_id"] != model.model_id or self.fixture["quant_params_sha256"] != model.quant_sha256:
            raise ValueError("Replay fixture belongs to a different model")

    def infer(self, payload, timeout_s=1.0):
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("Timeout must be finite and positive")
        validate_input(payload)
        if sha256(payload) != self.fixture["input_int8_sha256"]:
            raise ValueError("Replay only accepts the exact matching Golden input SHA256; no inference was performed")
        return {
            "collision_int32": checked_int32(self.fixture["collision_int32"]),
            "steering_int32": checked_int32(self.fixture["steering_int32"]),
        }


class PynqBackend(Backend):
    def __init__(self, model):
        raise NotImplementedError(
            "PYNQ backend is not implemented in this legacy app.py command. Use tools/run_board.py "
            "or run_hardware.sh with a verified overlay manifest. No hardware was accessed."
        )

    def infer(self, payload, timeout_s=1.0):
        raise NotImplementedError("Board integration pending")
