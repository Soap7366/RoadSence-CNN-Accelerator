"""Lightweight shared error types without importing reference preprocessing."""
class ControlError(RuntimeError):
    pass

class ControlBusyError(ControlError):
    pass

class ControlTimeout(TimeoutError):
    pass
