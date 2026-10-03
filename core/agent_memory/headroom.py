"""Disk headroom rule (memory-retrieval amendment v0.5.1 §4).

Every writer calls ``check_headroom`` BEFORE writing. Below the threshold it
skips with ``disk_low``; it never deletes anything to make room.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .errors import DISK_LOW

GB = 1024 ** 3
DEFAULT_MIN_FREE_BYTES = 5 * GB
DEFAULT_MIN_FREE_FRACTION = 0.10


def check_headroom(path: str | os.PathLike[str],
                   min_free_bytes: int | None = DEFAULT_MIN_FREE_BYTES,
                   min_free_fraction: float | None = DEFAULT_MIN_FREE_FRACTION) -> str | None:
    """Return ``"disk_low"`` when free space is below EITHER set threshold, else ``None``.

    ``path`` need not exist; the nearest existing ancestor is measured. A
    threshold of ``None`` is not applied (the Mac uses bytes, EC2 the fraction).
    If free space cannot be measured at all the answer is ``disk_low``: when in
    doubt, do not write.
    """
    probe = Path(path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return DISK_LOW
    if min_free_bytes is not None and usage.free < min_free_bytes:
        return DISK_LOW
    if min_free_fraction is not None and usage.total and usage.free / usage.total < min_free_fraction:
        return DISK_LOW
    return None
