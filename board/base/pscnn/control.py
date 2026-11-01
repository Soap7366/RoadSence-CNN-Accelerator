"""PS control lifecycle over a logical transport; no addresses or hardware imports.

This draft protocol requires completed reset and accepted-start acknowledgements.
It is a contract for a future adapter, not a claim about existing RTL registers.
Only one process may own that adapter/device while a session is in use.
"""
import math
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from numbers import Real

from .model import sha256
from .postprocess import register_word_to_int32
from .preprocess import validate_input

PROTOCOL = "pscnn.host.draft.v1"


class ControlError(RuntimeError):
    """Transport contract, frame, or lifecycle failure."""


class ControlBusyError(ControlError):
    """Another caller or an already running task owns the device."""


class ControlTimeout(TimeoutError):
    """The complete transaction or a bounded I/O operation exceeded its deadline."""


@dataclass(frozen=True)
class TransportIdentity:
    protocol: str
    model_id: str
    quant_params_sha256: str
    input_bytes: int = 4096


@dataclass(frozen=True)
class TransportStatus:
    busy: bool
    host_ready: bool
    result_valid: bool
    error: int = 0


class FrameTransport(ABC):
    """Logical host operations. Every implementation must bound its I/O by timeout_s.

    reset returns only after its synchronous reset has completed. start returns True
    only for a real, exactly-once accepted start pulse. read_result_words returns a
    coherent pair of held unsigned 32-bit words. These promises require an adapter;
    a write to an unacknowledged future control register does not satisfy them.
    """

    @abstractmethod
    def describe(self, timeout_s):
        raise NotImplementedError

    @abstractmethod
    def reset(self, timeout_s):
        raise NotImplementedError

    @abstractmethod
    def read_status(self, timeout_s):
        raise NotImplementedError

    @abstractmethod
    def write_input(self, offset, data, timeout_s):
        raise NotImplementedError

    @abstractmethod
    def read_input(self, offset, length, timeout_s):
        raise NotImplementedError

    @abstractmethod
    def start(self, timeout_s):
        raise NotImplementedError

    @abstractmethod
    def read_result_words(self, timeout_s):
        raise NotImplementedError


def _positive_seconds(value, name):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(name + " must be finite and positive")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(name + " must be finite and positive")
    return value


class ControlSession:
    """Validate, reset, load, verify, start once, and collect a held result.

    A local lock prevents concurrent calls through this instance. Device/process
    exclusivity must additionally be provided by the eventual concrete adapter.
    Each frame starts from an acknowledged reset and cleared result_valid, so a
    first poll that already sees a valid completed result is accepted.
    """

    def __init__(self, transport, model, chunk_bytes=256, poll_interval_s=0.001,
                 clock=time.monotonic, sleep=time.sleep, recovery_timeout_s=0.25):
        if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= 4096:
            raise ValueError("chunk_bytes must be an integer in [1, 4096]")
        self.transport = transport
        self.model = model
        self.chunk_bytes = chunk_bytes
        self.poll_interval_s = _positive_seconds(poll_interval_s, "poll_interval_s")
        self.recovery_timeout_s = _positive_seconds(recovery_timeout_s, "recovery_timeout_s")
        if not callable(clock) or not callable(sleep):
            raise ValueError("clock and sleep must be callable")
        self.clock, self.sleep = clock, sleep
        self._lock = threading.Lock()
        self._owns_device = False
        self.needs_recovery = False
        self.last_run = {}
        self.last_recovery = {}

    def _remaining(self, deadline, phase):
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise ControlTimeout("Deadline exceeded during " + phase)
        return remaining

    def _invoke(self, deadline, phase, report, operation, *arguments):
        report["phase"] = phase
        remaining = self._remaining(deadline, phase)
        value = operation(*arguments, timeout_s=remaining)
        # A slow operation returning a plausible value must not create false success.
        self._remaining(deadline, phase)
        return value

    def _identity(self, deadline, report):
        identity = self._invoke(deadline, "describe", report, self.transport.describe)
        if not isinstance(identity, TransportIdentity):
            raise ControlError("Transport describe must return TransportIdentity")
        if (identity.protocol != PROTOCOL or identity.model_id != self.model.model_id or
                identity.quant_params_sha256 != self.model.quant_sha256 or
                type(identity.input_bytes) is not int or identity.input_bytes != 4096):
            raise ControlError("Transport protocol/model/quantization/input size mismatch")

    def _status(self, deadline, phase, report, allow_error=False):
        status = self._invoke(deadline, phase, report, self.transport.read_status)
        if not isinstance(status, TransportStatus):
            raise ControlError("Transport read_status must return TransportStatus")
        if any(type(value) is not bool for value in
               (status.busy, status.host_ready, status.result_valid)):
            raise ControlError("Transport status flags must be bool")
        if type(status.error) is not int or not 0 <= status.error < (1 << 32):
            raise ControlError("Transport error must be an unsigned 32-bit integer")
        if status.busy and (status.host_ready or status.result_valid):
            raise ControlError("Contradictory transport status: busy with ready/valid")
        if status.error and not allow_error:
            raise ControlError("Transport reported error " + str(status.error))
        return status

    @staticmethod
    def _idle_clear(status):
        if status.busy or not status.host_ready or status.result_valid or status.error:
            raise ControlError("Expected idle, host ready, cleared result_valid and no error")

    def _reset(self, deadline, report, phase):
        report["reset_count"] += 1
        result = self._invoke(deadline, phase, report, self.transport.reset)
        if result is not None:
            raise ControlError("Transport reset must acknowledge completion by returning None")
        self._idle_clear(self._status(deadline, phase + "_status", report))

    def _automatic_recovery(self, report):
        report["recovery_attempted"] = True
        try:
            self._reset(self.clock() + self.recovery_timeout_s, report, "recovery_reset")
        except Exception as exc:
            self.needs_recovery = True
            report["recovery_succeeded"] = False
            report["recovery_error"] = str(exc)
            # Retain ownership knowledge: explicit recover may reset our stalled task.
        else:
            self.needs_recovery = False
            self._owns_device = False
            report["recovery_succeeded"] = True

    def infer(self, payload, timeout_s=1.0, verify_readback=True):
        if not self._lock.acquire(blocking=False):
            raise ControlBusyError("This control session already has an active caller")
        started = self.clock()
        report = {"input_int8_sha256": None, "bytes_written": 0, "bytes_read": 0,
                  "readback_requested": verify_readback, "readback_verified": False,
                  "reset_count": 0, "start_count": 0, "start_accepted": False,
                  "elapsed_s": 0.0, "recovery_attempted": False,
                  "recovery_succeeded": None, "phase": "validate"}
        self.last_run = report
        try:
            timeout_s = _positive_seconds(timeout_s, "timeout_s")
            if type(verify_readback) is not bool:
                raise ValueError("verify_readback must be bool")
            if not isinstance(payload, (bytes, bytearray, memoryview)):
                raise ValueError("Input payload must be bytes-like")
            # Freeze a caller's mutable bytearray before any transport I/O.
            payload = validate_input(bytes(payload))
            report["input_int8_sha256"] = sha256(payload)
            if self.needs_recovery:
                raise ControlError("Session needs explicit recover() before another frame")
            deadline = started + timeout_s
            self._identity(deadline, report)
            initial = self._status(deadline, "initial_status", report)
            if initial.busy:
                raise ControlBusyError("Device already busy; its task was not reset")
            self._owns_device = True
            self._reset(deadline, report, "frame_reset")

            for offset in range(0, 4096, self.chunk_bytes):
                chunk = payload[offset:offset + self.chunk_bytes]
                count = self._invoke(deadline, "write_input", report,
                                     self.transport.write_input, offset, chunk)
                if type(count) is int and 0 <= count <= len(chunk):
                    report["bytes_written"] += count
                if type(count) is not int or count != len(chunk):
                    raise ControlError("Input write did not accept the complete chunk at " + str(offset))
            if report["bytes_written"] != 4096:
                raise ControlError("A complete 4096-byte frame was not loaded")

            if verify_readback:
                for offset in range(0, 4096, self.chunk_bytes):
                    length = min(self.chunk_bytes, 4096 - offset)
                    data = self._invoke(deadline, "read_input", report,
                                        self.transport.read_input, offset, length)
                    if not isinstance(data, (bytes, bytearray, memoryview)):
                        raise ControlError("Input readback must be bytes-like")
                    data = bytes(data)
                    report["bytes_read"] += len(data)
                    if len(data) != length:
                        raise ControlError("Input readback length mismatch at " + str(offset))
                    if data != payload[offset:offset + length]:
                        raise ControlError("Input readback differs from frame at " + str(offset))
                report["readback_verified"] = True

            self._idle_clear(self._status(deadline, "before_start", report))
            report["start_count"] += 1  # Counts requests, including rejected/late acknowledgements.
            accepted = self._invoke(deadline, "start", report, self.transport.start)
            if type(accepted) is not bool or not accepted:
                raise ControlError("Transport did not acknowledge an accepted start")
            report["start_accepted"] = True
            while True:
                status = self._status(deadline, "wait_result", report)
                if status.result_valid:
                    break
                remaining = self._remaining(deadline, "wait_result")
                self.sleep(min(self.poll_interval_s, remaining))
                self._remaining(deadline, "wait_result")

            words = self._invoke(deadline, "read_result", report, self.transport.read_result_words)
            if not isinstance(words, (tuple, list)) or len(words) != 2:
                raise ControlError("Expected exactly two unsigned result words")
            try:
                result = {"collision_int32": register_word_to_int32(words[0]),
                          "steering_int32": register_word_to_int32(words[1])}
            except ValueError as exc:
                raise ControlError("Invalid unsigned result word: " + str(exc)) from exc
            final = self._status(deadline, "after_result", report)
            if final.busy or not final.result_valid:
                raise ControlError("Result_valid was lost or device changed while reading results")
            self._owns_device = False
            report["phase"] = "complete"
            report["status"] = "PASS"
            return result
        except Exception as exc:
            report["failed_phase"] = report["phase"]
            report["error"] = str(exc)
            if self._owns_device and not self.needs_recovery:
                self._automatic_recovery(report)
            report["phase"] = "failed"
            report["status"] = "FAIL"
            raise
        finally:
            report["elapsed_s"] = max(0.0, self.clock() - started)
            self._lock.release()

    def recover(self, timeout_s=0.25):
        """Reset a failed owned task; never reset an unrelated busy task.

        Explicit recovery verifies identity again. last_recovery records its own
        outcome, leaving the failed frame's last_run telemetry intact.
        """
        timeout_s = _positive_seconds(timeout_s, "timeout_s")
        if not self._lock.acquire(blocking=False):
            raise ControlBusyError("This control session already has an active caller")
        started = self.clock()
        report = {"phase": "recover", "status": "FAIL", "reset_count": 0, "elapsed_s": 0.0}
        self.last_recovery = report
        try:
            deadline = started + timeout_s
            self._identity(deadline, report)
            if not self._owns_device:
                status = self._status(deadline, "recovery_initial_status", report, allow_error=True)
                if status.busy:
                    raise ControlBusyError("Device already busy; its task was not reset")
            self._owns_device = True
            self._reset(deadline, report, "manual_reset")
            self.needs_recovery = False
            self._owns_device = False
            report["phase"], report["status"] = "complete", "PASS"
        except Exception as exc:
            if self._owns_device:
                self.needs_recovery = True
            report["error"] = str(exc)
            report["phase"] = "failed"
            raise
        finally:
            report["elapsed_s"] = max(0.0, self.clock() - started)
            self._lock.release()
