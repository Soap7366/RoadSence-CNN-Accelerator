"""Register adapter for the locally authored CNN AXI-Lite bridge v1.

Only register offsets are defined here. The caller supplies an exclusively owned
read(offset)/write(offset, value) object; no PYNQ import, physical address, Overlay
load or device access occurs when this module is imported or instantiated.

The bridge's declared identity is checked before ControlSession writes a frame.
That declaration does not independently prove the loaded model ROM contents.
A blocking register I/O call must return for Python to detect its elapsed timeout.
"""
import math
import time
from numbers import Integral, Real

from .control import (PROTOCOL, FrameTransport, TransportIdentity, TransportStatus,
                      ControlError, ControlTimeout)

MAGIC = 0x53434E4E
VERSION = 0x00010000
MODEL_COMMIT = "619308c77fd9af7d03bc89c494a33dda5d07386d"
MODEL_ID = "dual_task_baseline2_int8_qat@619308c77fd9"

REG_MAGIC = 0x000
REG_VERSION = 0x004
REG_CONTROL = 0x008
REG_STATUS = 0x00C
REG_ERROR = 0x010
REG_INPUT_COUNT = 0x014
REG_RESET_SEQ = 0x018
REG_START_SEQ = 0x01C
REG_COLLISION = 0x020
REG_STEERING = 0x024
REG_RESULT_SEQ = 0x028
REG_INPUT_BYTES = 0x02C
REG_QUANT_SHA = 0x030
REG_MODEL_COMMIT = 0x050
INPUT_WINDOW = 0x1000
INPUT_BYTES = 4096
STATUS_BUSY = 1
STATUS_READY = 2
STATUS_VALID = 4
STATUS_FRAME_LOADED = 8
STATUS_ERROR = 16


def _seconds(value, name):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(name + " must be finite and positive")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(name + " must be finite and positive")
    return value


class AxiLiteTransport(FrameTransport):
    """Implement the logical FrameTransport with concrete bridge-v1 registers.

    Input writes and reads require 4-byte alignment. Use ControlSession's default
    256-byte chunks, or another multiple of four. The input window packs four
    consecutive logical INT8 pixels in little-endian order in each AXI word.
    reset/start acceptance is determined by acknowledged sequence counters, not
    by the mere return of a register write or by observing a transient busy pulse.
    """

    def __init__(self, register_io, model, clock=time.monotonic, sleep=time.sleep,
                 poll_interval_s=0.001):
        if not callable(getattr(register_io, "read", None)) or not callable(getattr(register_io, "write", None)):
            raise ValueError("register_io must provide callable read/write methods")
        if not callable(clock) or not callable(sleep):
            raise ValueError("clock and sleep must be callable")
        self.io, self.model = register_io, model
        self.clock, self.sleep = clock, sleep
        self.poll_interval_s = _seconds(poll_interval_s, "poll_interval_s")
        self._expected_result_seq = None

    def _deadline(self, timeout_s):
        return self.clock() + _seconds(timeout_s, "timeout_s")

    def _remaining(self, deadline, phase):
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise ControlTimeout("AXI register deadline exceeded during " + phase)
        return remaining

    def _read(self, offset, deadline):
        phase = "read 0x{:03X}".format(offset)
        self._remaining(deadline, phase)
        try:
            value = self.io.read(offset)
        except (ControlError, ControlTimeout):
            raise
        except TimeoutError as exc:
            raise ControlTimeout(phase + " timed out: " + str(exc)) from exc
        except Exception as exc:
            raise ControlError(phase + " failed: " + str(exc)) from exc
        self._remaining(deadline, phase)
        if isinstance(value, bool) or not isinstance(value, Integral) or not 0 <= int(value) <= 0xFFFFFFFF:
            raise ControlError(phase + " did not return an unsigned 32-bit integer")
        return int(value)

    def _write(self, offset, value, deadline):
        phase = "write 0x{:03X}".format(offset)
        self._remaining(deadline, phase)
        if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
            raise ControlError("Register write value must be an unsigned 32-bit integer")
        try:
            self.io.write(offset, value)
        except (ControlError, ControlTimeout):
            raise
        except TimeoutError as exc:
            raise ControlTimeout(phase + " timed out: " + str(exc)) from exc
        except Exception as exc:
            raise ControlError(phase + " failed: " + str(exc)) from exc
        self._remaining(deadline, phase)

    def _pause(self, deadline, phase):
        self.sleep(min(self.poll_interval_s, self._remaining(deadline, phase)))
        self._remaining(deadline, phase)

    def _status(self, deadline):
        # The computation may finish between STATUS and ERROR reads. Retry a
        # changing status rather than treating a non-atomic sample as corruption.
        while True:
            before = self._read(REG_STATUS, deadline)
            error = self._read(REG_ERROR, deadline)
            after = self._read(REG_STATUS, deadline)
            if before != after:
                self._pause(deadline, "status snapshot")
                continue
            if before & ~0x1F:
                raise ControlError("Bridge STATUS contains unsupported reserved bits")
            busy = bool(before & STATUS_BUSY)
            ready = bool(before & STATUS_READY)
            valid = bool(before & STATUS_VALID)
            if bool(before & STATUS_ERROR) != bool(error):
                raise ControlError("Bridge STATUS error flag and ERROR register disagree")
            if busy and (ready or valid):
                raise ControlError("Bridge STATUS is busy with host_ready/result_valid")
            if error and ready:
                raise ControlError("Bridge STATUS must not be host_ready while ERROR is set")
            return TransportStatus(busy, ready, valid, error), before

    @staticmethod
    def _idle_clear(status):
        if status.error:
            raise ControlError("Bridge reported error " + str(status.error))
        if status.busy or not status.host_ready or status.result_valid:
            raise ControlError("Bridge must be idle, host_ready and result_valid clear")

    def _count(self, deadline):
        count = self._read(REG_INPUT_COUNT, deadline)
        if count > INPUT_BYTES:
            raise ControlError("Bridge INPUT_COUNT exceeds 4096 bytes")
        return count

    @staticmethod
    def _range(offset, length):
        if type(offset) is not int or type(length) is not int:
            raise ValueError("Input offset and length must be integers")
        if offset < 0 or length <= 0 or offset + length > INPUT_BYTES or offset % 4 or length % 4:
            raise ValueError("Input transfer must be 4-byte aligned and within bytes 0..4095")

    def describe(self, timeout_s):
        deadline = self._deadline(timeout_s)
        if self.model.model_id != MODEL_ID:
            raise ControlError("This bridge adapter is pinned to model " + MODEL_ID)
        if self._read(REG_MAGIC, deadline) != MAGIC:
            raise ControlError("AXI bridge MAGIC mismatch")
        if self._read(REG_VERSION, deadline) != VERSION:
            raise ControlError("AXI bridge VERSION mismatch")
        if self._read(REG_INPUT_BYTES, deadline) != INPUT_BYTES:
            raise ControlError("AXI bridge input size mismatch")
        quant_sha = "".join("{:08x}".format(self._read(REG_QUANT_SHA + 4 * index, deadline))
                            for index in range(8))
        commit = "".join("{:08x}".format(self._read(REG_MODEL_COMMIT + 4 * index, deadline))
                         for index in range(5))
        if commit != MODEL_COMMIT:
            raise ControlError("AXI bridge model commit mismatch")
        if quant_sha != self.model.quant_sha256:
            raise ControlError("AXI bridge quantization SHA256 mismatch")
        return TransportIdentity(PROTOCOL, self.model.model_id, quant_sha, INPUT_BYTES)

    def read_status(self, timeout_s):
        status, _ = self._status(self._deadline(timeout_s))
        return status

    def reset(self, timeout_s):
        deadline = self._deadline(timeout_s)
        self._expected_result_seq = None
        previous = self._read(REG_RESET_SEQ, deadline)
        expected = (previous + 1) & 0xFFFFFFFF
        self._write(REG_CONTROL, 1, deadline)
        while True:
            sequence = self._read(REG_RESET_SEQ, deadline)
            if sequence == expected:
                status, _ = self._status(deadline)
                self._idle_clear(status)
                if self._count(deadline) != 0:
                    raise ControlError("Acknowledged reset did not clear INPUT_COUNT")
                return None
            if sequence != previous:
                raise ControlError("RESET_SEQ changed by more than the requested reset")
            self._pause(deadline, "reset acknowledgement")

    def write_input(self, offset, data, timeout_s):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise ValueError("Input data must be bytes-like")
        data = bytes(data)
        self._range(offset, len(data))
        if any(value > 127 for value in data):
            raise ValueError("Grayscale input bytes must be in [0, 127]")
        deadline = self._deadline(timeout_s)
        self._idle_clear(self._status(deadline)[0])
        if self._count(deadline) != offset:
            raise ControlError("Input write must extend exactly the loaded byte prefix")
        for position in range(0, len(data), 4):
            value = int.from_bytes(data[position:position + 4], "little")
            self._write(INPUT_WINDOW + offset + position, value, deadline)
            if self._count(deadline) != offset + position + 4:
                raise ControlError("Bridge did not acknowledge all four input bytes")
        self._idle_clear(self._status(deadline)[0])
        return len(data)

    def read_input(self, offset, length, timeout_s):
        self._range(offset, length)
        deadline = self._deadline(timeout_s)
        self._idle_clear(self._status(deadline)[0])
        if self._count(deadline) < offset + length:
            raise ControlError("Input read exceeds the completely loaded byte prefix")
        data = bytearray()
        for position in range(0, length, 4):
            value = self._read(INPUT_WINDOW + offset + position, deadline)
            data.extend(value.to_bytes(4, "little"))
        self._idle_clear(self._status(deadline)[0])
        return bytes(data)

    def start(self, timeout_s):
        deadline = self._deadline(timeout_s)
        status, flags = self._status(deadline)
        self._idle_clear(status)
        if not flags & STATUS_FRAME_LOADED or self._count(deadline) != INPUT_BYTES:
            raise ControlError("A complete 4096-byte frame is required before start")
        previous = self._read(REG_START_SEQ, deadline)
        expected = (previous + 1) & 0xFFFFFFFF
        self._expected_result_seq = None
        self._write(REG_CONTROL, 2, deadline)
        while True:
            sequence = self._read(REG_START_SEQ, deadline)
            status, _ = self._status(deadline)
            if status.error:
                raise ControlError("Bridge reported error during start: " + str(status.error))
            if sequence == expected:
                if self._count(deadline) != 0:
                    raise ControlError("Accepted start did not consume the loaded input frame")
                self._expected_result_seq = expected
                return True
            if sequence != previous:
                raise ControlError("START_SEQ changed by more than the requested start")
            self._pause(deadline, "start acknowledgement")

    def read_result_words(self, timeout_s):
        deadline = self._deadline(timeout_s)
        expected = self._expected_result_seq
        if expected is None:
            raise ControlError("No accepted start is associated with this transport result")
        status, _ = self._status(deadline)
        if status.error or status.busy or not status.result_valid:
            raise ControlError("The accepted start has no valid completed result")
        before = self._read(REG_RESULT_SEQ, deadline)
        if before != expected:
            raise ControlError("RESULT_SEQ does not belong to the accepted start")
        collision = self._read(REG_COLLISION, deadline)
        steering = self._read(REG_STEERING, deadline)
        after = self._read(REG_RESULT_SEQ, deadline)
        final, _ = self._status(deadline)
        if before != after or after != expected or final.error or final.busy or not final.result_valid:
            raise ControlError("Result sequence/status changed while reading the result pair")
        return collision, steering
