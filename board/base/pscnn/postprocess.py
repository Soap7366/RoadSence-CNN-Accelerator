"""Two signed INT32 results -> model-space steering and collision score."""
import math
from numbers import Integral


def checked_int32(value):
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError("Raw output must be an integer, not a float or unsigned register word")
    value = int(value)
    if not -(1 << 31) <= value < (1 << 31):
        raise ValueError("Raw output is outside signed INT32 range")
    return value


def register_word_to_int32(word):
    """Convert one future 32-bit MMIO register value to signed INT32."""
    if isinstance(word, bool) or not isinstance(word, Integral) or not 0 <= int(word) < (1 << 32):
        raise ValueError("Register word must be in [0, 2^32-1]")
    word = int(word)
    return word - (1 << 32) if word & (1 << 31) else word


def decode_outputs(collision_int32, steering_int32, model, threshold=None):
    collision_int32 = checked_int32(collision_int32)
    steering_int32 = checked_int32(steering_int32)
    logit = collision_int32 * model.collision_scale
    if logit >= 0:
        risk = 1.0 / (1.0 + math.exp(-logit))
    else:
        exponential = math.exp(logit)
        risk = exponential / (1.0 + exponential)
    result = {
        "collision_int32": collision_int32,
        "steering_int32": steering_int32,
        "collision_logit": logit,
        "risk_score": risk,
        "steering_value": steering_int32 * model.steering_scale,
        "steering_units": "rad_label_space",
    }
    if threshold is not None:
        if not math.isfinite(threshold) or not 0 < threshold < 1:
            raise ValueError("Threshold must be finite and strictly between 0 and 1")
        result["risk_threshold"] = threshold
        result["collision_class"] = int(risk >= threshold)
    return result
