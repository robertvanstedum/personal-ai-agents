"""Workshop events and the derived state: a compatibility facade.

The implementation moved to ``core/workshop_journal`` (Workshop build, Unit 1): one locked, recovering, durable writer for every
caller, strict reading, and a reducer that no longer lets a label close an owner question. This module keeps the names the
screen, the observer and the CLI import, with the v1 vocabulary and ``validate`` unchanged. Stdlib only, so the Mac's scripts
and the portal share it. Do not add a second implementation here.
"""
from __future__ import annotations

from core.workshop_journal.legacy import (ACTORS, CLOCK_AHEAD, KINDS, STAGES, STALE_AFTER, VERSION, Workshop,  # noqa: F401
                                          iso, load_state, now, parse, reduce, validate)

__all__ = ["Workshop", "validate", "reduce", "load_state", "ACTORS", "KINDS", "STAGES", "STALE_AFTER", "CLOCK_AHEAD", "iso",
           "now", "parse", "VERSION"]
