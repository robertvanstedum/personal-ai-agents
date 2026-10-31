"""Rooms R3b (docs/specs/minimoi-connected-work/ROOMS_R3.md §3): Records'
browser sign-in becomes a server-side session that logout and credential
revocation really end. New table only: older Records code ignores it, so R1's
data-preserving rollback still holds; the rollback procedure rotates the
cookie signing key so a revoked cookie cannot come back under older code.

Bearer credentials (workers, connectors, roomctl) never pass through here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets

SCHEMA = """
CREATE TABLE IF NOT EXISTS web_sessions(
    id TEXT PRIMARY KEY,principal TEXT NOT NULL,credential_id TEXT NOT NULL,
    created TEXT NOT NULL,expires TEXT NOT NULL,revoked TEXT);
CREATE INDEX IF NOT EXISTS web_sessions_credential ON web_sessions(credential_id);
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


class WebSessions:
    def __init__(self, connect, lifetime=timedelta(hours=8)):
        self.connect = connect
        self.lifetime = lifetime
        with connect() as db:
            db.executescript(SCHEMA)

    def start(self, principal, credential_id):
        sid = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + self.lifetime).isoformat()
        with self.connect() as db:
            db.execute("INSERT INTO web_sessions VALUES(?,?,?,?,?,NULL)", (sid, principal, credential_id, _now(), expires))
        return sid

    def valid(self, sid, credential_id):
        if not isinstance(sid, str) or not sid:
            return False
        with self.connect() as db:
            row = db.execute("SELECT credential_id,expires,revoked FROM web_sessions WHERE id=?", (sid,)).fetchone()
        return bool(row and not row["revoked"] and row["expires"] > _now() and row["credential_id"] == credential_id)

    def end(self, sid):
        if isinstance(sid, str) and sid:
            with self.connect() as db:
                db.execute("UPDATE web_sessions SET revoked=COALESCE(revoked,?) WHERE id=?", (_now(), sid))

    @staticmethod
    def end_for_credential(db, credential_id):
        """Inside the revoking transaction (platform_access.revoke)."""
        db.execute("UPDATE web_sessions SET revoked=COALESCE(revoked,?) WHERE credential_id=? AND revoked IS NULL",
                   (_now(), credential_id))
