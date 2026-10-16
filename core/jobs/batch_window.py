"""Opt-in nightly phase admission; finish atomic writes and cleanup safely.

No setting means unchanged behavior for owner CLI and live Ask. A runtime job
sets the exact 06:00 Chicago boundary for its current day. The guard admits no
new phase past that point, but never interrupts plaintext cleanup or an atomic
record publication. This is not a hard wall-clock termination mechanism.
"""
import math
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

ENV = 'MOI_BATCH_DEADLINE_EPOCH'

class BatchDeadline(RuntimeError):
    def __init__(self, code='outside_window'):
        super().__init__(code)
        self.code = code


def remaining(limit=3600, *, environ=None, clock=time.time):
    environ = os.environ if environ is None else environ
    raw = environ.get(ENV)
    if raw is None:
        return limit
    try:
        deadline = float(raw)
        if not math.isfinite(deadline) or deadline <= 0:
            raise ValueError
        boundary = datetime.fromtimestamp(deadline, ZoneInfo('America/Chicago'))
        if (boundary.hour, boundary.minute, boundary.second, boundary.microsecond) != (6, 0, 0, 0):
            raise ValueError
        now = clock()
        local = datetime.fromtimestamp(now, ZoneInfo('America/Chicago'))
        if boundary.date() != local.date():
            raise ValueError
    except (TypeError, ValueError, OverflowError, OSError):
        raise BatchDeadline('batch_deadline_invalid') from None
    left = deadline - now
    if left <= 0:
        raise BatchDeadline()
    return min(limit, left)
