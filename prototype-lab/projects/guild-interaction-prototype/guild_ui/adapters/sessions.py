"""Rooms/Records sessions. Live = a Records SQLite opened read-only (mode=ro),
reading the `rooms` table (which holds sessions) directly. Never through
Records Store(), whose constructor creates and migrates schema."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .contract import INVALID, READ_FAILED, SourceResult, live_ok, live_unknown, not_configured, sample


class LiveSessions:
    def __init__(self, db_path: str | None):
        self.db_path = db_path

    def list_sessions(self, limit: int = 5) -> SourceResult:
        if not self.db_path:
            return not_configured("GUILD_RECORDS_DB", "Records SQLite (GUILD_RECORDS_DB)")
        path = Path(self.db_path)
        evidence = f"{path.name} · rooms (read-only)"
        if not path.is_file():
            return live_unknown(READ_FAILED, f"{path.name} not found", evidence)
        try:
            con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=1)
            try:
                rows = con.execute(
                    "SELECT id, title, state, updated FROM rooms ORDER BY updated DESC LIMIT ?", (limit,)
                ).fetchall()
            finally:
                con.close()
        except sqlite3.OperationalError as exc:
            reason = INVALID if "no such" in str(exc) else READ_FAILED
            return live_unknown(reason, f"{path.name}: {exc}", evidence)
        except sqlite3.DatabaseError as exc:
            return live_unknown(INVALID, f"{path.name}: {exc}", evidence)
        data = [{"id": r[0], "title": r[1], "state": r[2], "updated": r[3], "people": ""} for r in rows]
        return live_ok(data, evidence, fresh_for_s=300)


class SampleSessions:
    def __init__(self, path: Path):
        self.path = Path(path)

    def list_sessions(self, limit: int = 5) -> SourceResult:
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        return sample(doc["sessions"][:limit], f"fixtures/{self.path.name}", doc.get("observed_at"))
