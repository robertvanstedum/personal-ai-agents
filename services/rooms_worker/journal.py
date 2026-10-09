"""The meeting turn journal: journal before inference, saved payload after.

TurnJournal below is a byte-for-byte copy of the class in
prototype-lab/projects/project-records-room-poc/integration/cos_room_responder.py
(ROOMS_R1.md §3.7: "TurnJournal unchanged"); tests/test_worker.py fails if the
two ever differ. peek() is the worker's own read-only look, outside the class.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat


class RoomBridgeError(RuntimeError):
    """Same name as the Records module's error, so the copied class is verbatim."""


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class TurnJournal:
    """Single-origin execution journal, not a second authoritative room store."""

    def __init__(self, root):
        self.root = Path(root)
        if self.root.is_symlink():
            raise RoomBridgeError("Meeting journal must not be a symlink.")
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        if self.root.stat().st_mode & 0o077:
            raise RoomBridgeError("Meeting journal must be owner-private.")
        self.path = self.root / "turns.sqlite3"
        # Pre-create with private permissions rather than a global umask change.
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise RoomBridgeError("Meeting journal file must be private and regular.")
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS turns(
                operation TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                state TEXT NOT NULL, payload TEXT, created TEXT NOT NULL,
                snapshot_metadata TEXT NOT NULL DEFAULT '{}')""")
            if "snapshot_metadata" not in {r[1] for r in db.execute("PRAGMA table_info(turns)")}:
                db.execute("ALTER TABLE turns ADD COLUMN snapshot_metadata TEXT NOT NULL DEFAULT '{}'")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, operation, fingerprint, metadata=None):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM turns WHERE operation=?", (operation,)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise RoomBridgeError("Request ID already belongs to different meeting content.")
                if old["state"] == "generated":
                    return json.loads(old["payload"])
                raise RoomBridgeError("This model turn is in progress, failed, or uncertain. It will not be run again automatically; inspect it before requesting a new turn.")
            db.execute("INSERT INTO turns(operation,fingerprint,state,payload,created,snapshot_metadata) VALUES(?,?,'started',NULL,?,?)",
                       (operation, fingerprint, datetime.now(timezone.utc).isoformat(), encoded(metadata or {})))
        return None

    def generated(self, operation, payload):
        with self.db() as db:
            db.execute("UPDATE turns SET state='generated',payload=? WHERE operation=? AND state='started'",
                       (encoded(payload), operation))


def peek(journal, operation):
    """(state, payload) of a journal entry without creating one: None, 'started' or 'generated'."""
    with journal.db() as db:
        row = db.execute("SELECT state,payload FROM turns WHERE operation=?", (operation,)).fetchone()
    if row is None:
        return None, None
    return row["state"], (json.loads(row["payload"]) if row["payload"] else None)
