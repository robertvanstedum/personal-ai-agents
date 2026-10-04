"""Fixed failure codes (agent-memory v0.4 §6, amendment §4).

The copier records *only* these codes. Error text, exception messages, file
names and matched text are never logged, stored or returned, because they can
carry file content (T9).
"""
from __future__ import annotations

DOCKER_UNREACHABLE = "docker_unreachable"
DOCKER_TIMEOUT = "docker_timeout"
COPY_INCOMPLETE = "copy_incomplete"
DISK_WRITE_FAILED = "disk_write_failed"
MODE_UNREADABLE = "mode_unreadable"
DISK_LOW = "disk_low"
INTERNAL = "internal"
BUSY = "busy"                      # another run of this source holds its lock: skipped, nothing touched

CODES = frozenset({DOCKER_UNREACHABLE, DOCKER_TIMEOUT, COPY_INCOMPLETE,
                   DISK_WRITE_FAILED, MODE_UNREADABLE, DISK_LOW, INTERNAL, BUSY})


class CopierError(Exception):
    """A failure that already knows its fixed code. The message *is* the code."""

    def __init__(self, code: str) -> None:
        if code not in CODES:
            code = INTERNAL
        super().__init__(code)
        self.code = code


def classify(exc: BaseException, stage: str) -> str:
    """Map any exception to one fixed code, never looking at its text.

    ``stage`` is ``"scan"`` (reading the source) or ``"write"`` (publishing).
    """
    if isinstance(exc, CopierError):
        return exc.code
    if stage == "scan":
        if isinstance(exc, PermissionError):
            return MODE_UNREADABLE
        if isinstance(exc, TimeoutError):
            return DOCKER_TIMEOUT
        if isinstance(exc, OSError):
            return COPY_INCOMPLETE
    elif stage == "write" and isinstance(exc, OSError):
        return DISK_WRITE_FAILED
    return INTERNAL
