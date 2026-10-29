"""Numeric evidence used by the operator-only plain and streaming cost probes."""
import math


def measurement(value):
    """Return a finite nonnegative number, or None for unknown/invalid evidence."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def total(values, *, require_all=False):
    values = [measurement(value) for value in values]
    if require_all and any(value is None for value in values):
        return None
    known = [value for value in values if value is not None]
    if not known:
        return None
    try:
        return measurement(sum(known))
    except OverflowError:
        return None


def probe_cap(value, maximum):
    """A positive measured-spend stop threshold, bounded by the existing maximum."""
    try:
        cap = None if isinstance(value, bool) else measurement(float(value))
    except (TypeError, ValueError, OverflowError):
        cap = None
    if cap is None or cap <= 0:
        raise ValueError('cap must be a finite positive number')
    return min(cap, maximum)
