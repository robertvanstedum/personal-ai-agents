"""Guest access requests as one domain service over both stores.

Two stores hold pending access requests today: Postgres ``guild.guest_requests``
(the website/Telegram flow) and the file-backed registration list
``auth/pending.json`` (the /register flow). Every request is addressed by a
store-qualified reference, ``guild:<id>`` or ``pending:<token>``, so a caller can
never act on the wrong store by guessing.

This module owns listing, the read-only summary the morning brief reads, exact
target resolution, and rejection with read-back verification. Granting is a
cross-store operation (account file, email, notification) and is built
separately as a state machine. Route wiring and the MC action adapter call
these functions; they do not reimplement the rules.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime

from . import auth

GUILD = "guild"
PENDING_FILE = "pending"

_SELECT_GUILD = (
    "SELECT id, name, email, domain, status, requested_at, actioned_at"
    " FROM guild.guest_requests"
)


@dataclass(frozen=True)
class GuestRequest:
    ref: str
    store: str
    name: str
    email: str
    domain: str
    status: str
    requested_at: str | None


@dataclass(frozen=True)
class Outcome:
    """``done`` = this call changed the request and the read-back confirms it.
    ``already_applied`` = it was already in the target state (safe repeat).
    ``conflict`` = it is in another terminal state (e.g. granted); nothing changed.
    ``not_found`` / ``invalid_ref`` = no such request; nothing changed."""
    status: str
    ref: str
    verified_state: dict = field(default_factory=dict)


class PostgresStore:
    """Small adapter so the service can be tested without a database."""

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or os.environ["DATABASE_URL"]

    def _connect(self):
        import psycopg2
        import psycopg2.extras
        return psycopg2.connect(self.dsn, cursor_factory=psycopg2.extras.RealDictCursor)

    def query(self, sql: str, params=()) -> list[dict]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def reject_if_requested(self, request_id: int) -> tuple[dict | None, dict | None]:
        """One transaction: conditional UPDATE, then read back the row."""
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE guild.guest_requests SET status='rejected', actioned_at=NOW()"
                        " WHERE id=%s AND status='requested' RETURNING id",
                        (request_id,),
                    )
                    changed = cur.fetchone()
                    cur.execute(_SELECT_GUILD + " WHERE id=%s", (request_id,))
                    row = cur.fetchone()
            return (dict(changed) if changed else None), (dict(row) if row else None)
        finally:
            conn.close()


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _from_guild(row: dict) -> GuestRequest:
    return GuestRequest(
        ref=f"{GUILD}:{row['id']}", store=GUILD, name=row.get("name") or "",
        email=row.get("email") or "", domain=row.get("domain") or "",
        status=row.get("status") or "", requested_at=_iso(row.get("requested_at")),
    )


def _from_pending(entry: dict) -> GuestRequest:
    return GuestRequest(
        ref=f"{PENDING_FILE}:{entry['token']}", store=PENDING_FILE,
        name=entry.get("display_name") or "", email=entry.get("email") or "",
        domain="", status="requested", requested_at=entry.get("requested_at"),
    )


def list_requests(db=None, status: str = "requested") -> list[GuestRequest]:
    """Requests in ``status`` from both stores, oldest first. The file store
    only holds open registrations, so it contributes only to ``requested``."""
    db = db or PostgresStore()
    rows = db.query(_SELECT_GUILD + " WHERE status=%s ORDER BY requested_at", (status,))
    found = [_from_guild(r) for r in rows]
    if status == "requested":
        found += [_from_pending(p) for p in auth.load_pending()]
    return sorted(found, key=lambda r: r.requested_at or "")


def pending_summary(db=None) -> dict:
    """Read-only counts for the standing brief. Unlike the CoS nudge query this
    never writes, and an unreachable store is reported, not counted as zero."""
    db = db or PostgresStore()
    summary = {"coverage": "complete", "stores": {}}
    try:
        rows = db.query(
            "SELECT count(*) AS n, min(requested_at) AS oldest"
            " FROM guild.guest_requests WHERE status='requested'"
        )
        row = rows[0] if rows else {"n": 0, "oldest": None}
        summary["stores"][GUILD] = {"count": int(row["n"]), "oldest": _iso(row["oldest"])}
    except Exception:
        summary["stores"][GUILD] = {"count": None, "oldest": None, "error": "unavailable"}
        summary["coverage"] = "partial"
    try:
        pending = auth._load_json("pending.json", strict=True).get("pending", [])
        oldest = min((p.get("requested_at") or "" for p in pending), default=None) or None
        summary["stores"][PENDING_FILE] = {"count": len(pending), "oldest": oldest}
    except auth.AuthStoreError:
        summary["stores"][PENDING_FILE] = {"count": None, "oldest": None, "error": "unreadable"}
        summary["coverage"] = "partial"
    return summary


def resolve(identifier: str, db=None) -> list[GuestRequest]:
    """Open requests matching a name or email exactly (case-insensitive).
    Returns every match; the caller asks for clarification when there is not
    exactly one. Never guesses with partial matches."""
    wanted = identifier.strip().lower()
    if not wanted:
        return []
    return [r for r in list_requests(db)
            if r.name.strip().lower() == wanted or r.email.strip().lower() == wanted]


def get(ref: str, db=None) -> GuestRequest | None:
    store, _, key = ref.partition(":")
    if store == GUILD and key.isdigit():
        db = db or PostgresStore()
        rows = db.query(_SELECT_GUILD + " WHERE id=%s", (int(key),))
        return _from_guild(rows[0]) if rows else None
    if store == PENDING_FILE and key:
        entry = next((p for p in auth.load_pending() if p.get("token") == key), None)
        return _from_pending(entry) if entry else None
    return None


def reject(ref: str, db=None) -> Outcome:
    """Reject one open request by store-qualified reference and verify it.

    Only a ``requested`` row changes; a granted request is never flipped to
    rejected (revoking an account is a different action). The verified state
    records that the request is no longer eligible for pending nudges, which
    select ``status='requested'`` only."""
    store, _, key = ref.partition(":")
    if store == GUILD and key.isdigit():
        db = db or PostgresStore()
        changed, row = db.reject_if_requested(int(key))
        if row is None:
            return Outcome("not_found", ref)
        state = {"store": GUILD, "status": row["status"],
                 "actioned_at": _iso(row.get("actioned_at")),
                 "nudge_eligible": row["status"] == "requested"}
        if changed and row["status"] == "rejected":
            return Outcome("done", ref, state)
        if row["status"] == "rejected":
            return Outcome("already_applied", ref, state)
        return Outcome("conflict", ref, state)
    if store == PENDING_FILE and key:
        removed = auth.reject_pending(key)
        still_there = any(p.get("token") == key for p in auth.load_pending())
        if still_there:
            return Outcome("conflict", ref, {"store": PENDING_FILE, "status": "requested"})
        state = {"store": PENDING_FILE, "status": "removed", "nudge_eligible": False}
        return Outcome("done" if removed else "not_found", ref, state if removed else {})
    return Outcome("invalid_ref", ref)
