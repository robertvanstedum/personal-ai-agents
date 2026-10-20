"""Worker side of owner pause/resume and the emergency disable (standing spec §§3.1, 7.3; harness
v0.3.1 §14.2).

The platform's owner-authenticated control endpoint (Codex) records the desired state in a small
versioned document the worker can only read: ``mc-standing-control/1``
``{version, state: enabled|paused, request_id, updated_at}``. The worker checks it between phases
and before each send, and writes an acknowledgement of the version it actually applied into its
own spool, so the endpoint can report "paused" only once the worker has confirmed it.

Fail-safe: a missing, unreadable or malformed control document means **paused**, never enabled.
The emergency disable is a separate file whose mere presence stops every new read, model call
and send at the next checkpoint; it does not depend on the control document or the endpoint.
"""
from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "mc-standing-control/1"
MAX_BYTES = 4096


class Control:
    def __init__(self, control_file: str | Path, disable_file: str | Path, spool=None):
        self.control_file = Path(control_file)
        self.disable_file = Path(disable_file)
        self.spool = spool

    def _read(self) -> dict | None:
        try:
            info = os.lstat(self.control_file)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                return None
            doc = json.loads(self.control_file.read_bytes())
        except (OSError, ValueError):
            return None
        if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
            return None
        version = doc.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            return None
        if doc.get("state") not in ("enabled", "paused"):
            return None
        return doc

    def killed(self) -> bool:
        """Emergency disable: present (even unreadable) means stop."""
        return os.path.lexists(self.disable_file)

    def paused(self) -> bool:
        doc = self._read()
        paused = doc is None or doc["state"] == "paused"
        if self.spool is not None:
            self._acknowledge(doc, paused)
        return paused

    def _acknowledge(self, doc: dict | None, paused: bool):
        ack = {"schema": "mc-standing-control-ack/1",
               "applied_version": doc["version"] if doc else None,
               "applied_state": "paused" if paused else "enabled",
               "reason": None if doc else "control_unreadable_fail_safe",
               "effective_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        previous = self.spool.read("cycles", "control-ack")
        if not previous or (previous.get("applied_version"), previous.get("applied_state"), previous.get("reason")) \
                != (ack["applied_version"], ack["applied_state"], ack["reason"]):
            self.spool.put_state("cycles", "control-ack", ack)

    def acknowledged(self) -> dict | None:
        return self.spool.read("cycles", "control-ack") if self.spool is not None else None
