"""The owner's Stop, fenced in this process before anything can fail (spec v1.2 §7.4; Codex M1 review P1-1 edges).

Stop first records the turn's ledger claim here, in memory, then writes the durable revocation. The tool listener runs
as a thread of the same portal process (docker/Dockerfile.portal: one threaded ``python minimoi_portal/app.py``), so
admission checks this fence even when the durable write fails, and even before the turn's binding exists. The durable
revocation remains the record other processes and restarts rely on; a multi-process portal would need it alone.
"""
from __future__ import annotations

import threading
from collections import OrderedDict

_LOCK = threading.Lock()
_FENCED: "OrderedDict[tuple[int, int], None]" = OrderedDict()
_MAX = 10000                                  # far more turns than a process sees; the oldest are long over


def fence(ledger_id: int, generation: int) -> None:
    with _LOCK:
        _FENCED[(ledger_id, generation)] = None
        _FENCED.move_to_end((ledger_id, generation))
        while len(_FENCED) > _MAX:
            _FENCED.popitem(last=False)


def fenced(ledger_id: int, generation: int) -> bool:
    with _LOCK:
        return (ledger_id, generation) in _FENCED
