"""RAM-only transport for PS control checks. No CNN arithmetic or hardware access."""
import math
from .control import FrameTransport, TransportIdentity, TransportStatus, PROTOCOL


class MemoryTransport(FrameTransport):
    """Simulated 4096-byte input store and fixed diagnostic result words.

    These words exercise unsigned-register to signed-INT32 conversion. They are
    unrelated to the input image and MUST NOT be decoded as a CNN prediction.
    """

    simulation_only = True
    diagnostic_words = (0xFFFFFFFF, 0x80000000)

    def __init__(self, model):
        self.identity = TransportIdentity(PROTOCOL, model.model_id, model.quant_sha256, 4096)
        self.memory = bytearray(4096)
        self.loaded = bytearray(4096)
        self.busy = False
        self.valid = False
        self.remaining_polls = 0
        self.starts = 0
        self.resets = 0
        self.events = []

    @staticmethod
    def _timeout(timeout_s):
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
            raise ValueError("Timeout must be a positive finite number")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("Timeout must be a positive finite number")

    @staticmethod
    def _range(offset, length):
        if type(offset) is not int or type(length) is not int:
            raise ValueError("Input offset and length must be integers")
        if offset < 0 or length <= 0 or offset + length > 4096:
            raise ValueError("Input accesses must stay within logical byte addresses 0..4095")

    def describe(self, timeout_s):
        self._timeout(timeout_s)
        return self.identity

    def reset(self, timeout_s):
        self._timeout(timeout_s)
        self.memory[:] = bytes(4096)
        self.loaded[:] = bytes(4096)
        self.busy = self.valid = False
        self.remaining_polls = 0
        self.resets += 1
        self.events.append({"operation": "reset_acknowledged"})

    def read_status(self, timeout_s):
        self._timeout(timeout_s)
        if self.busy:
            self.remaining_polls -= 1
            if self.remaining_polls <= 0:
                self.busy = False
                self.valid = True
        return TransportStatus(busy=self.busy, host_ready=not self.busy,
                               result_valid=self.valid, error=0)

    def write_input(self, offset, data, timeout_s):
        self._timeout(timeout_s)
        if not isinstance(data, bytes):
            raise ValueError("Transport writes require immutable bytes")
        self._range(offset, len(data))
        if self.busy or self.valid:
            raise RuntimeError("Reset an idle simulated transport before loading another frame")
        self.memory[offset:offset + len(data)] = data
        self.loaded[offset:offset + len(data)] = bytes([1]) * len(data)
        self.events.append({"operation": "write_input", "offset": offset, "bytes": len(data)})
        return len(data)

    def read_input(self, offset, length, timeout_s):
        self._timeout(timeout_s)
        self._range(offset, length)
        if self.busy:
            raise RuntimeError("Cannot read simulated input while busy")
        self.events.append({"operation": "read_input", "offset": offset, "bytes": length})
        return bytes(self.memory[offset:offset + length])

    def start(self, timeout_s):
        self._timeout(timeout_s)
        if self.busy or self.valid or not all(self.loaded):
            return False
        self.starts += 1
        self.busy = True
        self.remaining_polls = 2
        self.events.append({"operation": "start_accepted", "cnn_arithmetic": False})
        return True

    def read_result_words(self, timeout_s):
        self._timeout(timeout_s)
        if self.busy or not self.valid:
            raise RuntimeError("Simulated diagnostic words are not valid yet")
        self.events.append({"operation": "read_fixed_diagnostic_words"})
        return self.diagnostic_words
