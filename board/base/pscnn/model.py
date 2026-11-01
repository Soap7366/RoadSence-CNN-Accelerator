"""Load the pinned export; never guess a scale when configuration is absent."""
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def sha256(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    input_scale: float
    collision_scale: float
    steering_scale: float
    quant_sha256: str
    image_size: int = 64

    @classmethod
    def load(cls, directory=None):
        directory = Path(directory) if directory else PROJECT_ROOT / "config"
        contract = json.loads((directory / "model_contract.json").read_text(encoding="utf-8"))
        payload = (directory / "quant_params.json").read_bytes()
        if sha256(payload) != contract["quant_params_sha256"]:
            raise ValueError("quant_params.json SHA256 differs from the pinned model contract")
        q = json.loads(payload.decode("utf-8"))
        if contract["input_shape"] != [1, 1, 64, 64] or q["qrange"] != [-127, 127]:
            raise ValueError("Unsupported input shape or INT8 range")
        scales = [q["activation_scales"]["input"],
                  q["layers"]["collision"]["logit_scales"][0],
                  q["layers"]["steering"]["logit_scales"][0]]
        if any(not math.isfinite(float(s)) or float(s) <= 0 for s in scales):
            raise ValueError("Scales must be finite and positive")
        if float(scales[0]) != 1.0 / 127:
            raise ValueError("This framework only supports the pinned grayscale input scale")
        return cls(contract["model_id"], *map(float, scales), sha256(payload))
