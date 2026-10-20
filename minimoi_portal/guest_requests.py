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
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

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
    ``not_found`` / ``invalid_ref`` = no such request; nothing changed.
    ``unknown`` = a store could not be read or the write was not confirmed;
    reconcile by reading back before any retry."""
    status: str
    ref: str
    verified_state: dict = field(default_factory=dict)
    # A one-time temporary password for the approval email, returned to the caller once and
    # never stored or logged. Excluded from repr so it cannot leak into logs or receipts.
    delivery_secret: str | None = field(default=None, repr=False, compare=False)


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


def _grant_if_requested(self, request_id: int) -> tuple[dict | None, dict | None]:
    conn = self._connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE guild.guest_requests SET status='granted', actioned_at=NOW()"
                    " WHERE id=%s AND status='requested' RETURNING id",
                    (request_id,),
                )
                changed = cur.fetchone()
                cur.execute(_SELECT_GUILD + " WHERE id=%s", (request_id,))
                row = cur.fetchone()
        return (dict(changed) if changed else None), (dict(row) if row else None)
    finally:
        conn.close()


PostgresStore.grant_if_requested = _grant_if_requested


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


class StoreUnavailable(RuntimeError):
    """A request store could not be read. Callers must treat coverage as
    incomplete: no name-resolved effect and no not-found verdict may rest on it."""

    def __init__(self, store: str):
        super().__init__(f"{store} store unavailable")
        self.store = store


@dataclass(frozen=True)
class Resolution:
    """Exact matches plus the coverage they were found with. Only a
    ``complete`` resolution with exactly one match may target an effect."""
    matches: tuple
    complete: bool
    unavailable: tuple = ()

    @property
    def unique(self) -> GuestRequest | None:
        return self.matches[0] if self.complete and len(self.matches) == 1 else None


def _db(db):
    if db is not None:
        return db
    try:
        return PostgresStore()
    except Exception:
        raise StoreUnavailable(GUILD) from None


def _guild_rows(db, sql: str, params=()) -> list[dict]:
    try:
        return _db(db).query(sql, params)
    except StoreUnavailable:
        raise
    except Exception:
        raise StoreUnavailable(GUILD) from None


def _pending_entries() -> list[dict]:
    try:
        entries = auth.load_pending_strict()
    except (auth.AuthStoreError, OSError):
        raise StoreUnavailable(PENDING_FILE) from None
    if not isinstance(entries, list) or not all(isinstance(e, dict) and "token" in e for e in entries):
        raise StoreUnavailable(PENDING_FILE)
    return entries


def _collect(db, status: str) -> tuple[list[GuestRequest], tuple]:
    found, unavailable = [], []
    try:
        rows = _guild_rows(db, _SELECT_GUILD + " WHERE status=%s ORDER BY requested_at", (status,))
        found += [_from_guild(r) for r in rows]
    except StoreUnavailable as exc:
        unavailable.append(exc.store)
    if status == "requested":
        try:
            found += [_from_pending(p) for p in _pending_entries()]
        except StoreUnavailable as exc:
            unavailable.append(exc.store)
    return sorted(found, key=lambda r: r.requested_at or ""), tuple(unavailable)


def list_requests(db=None, status: str = "requested", allow_partial: bool = False) -> list[GuestRequest]:
    """Requests in ``status`` from both stores, oldest first. The file store
    only holds open registrations, so it contributes only to ``requested``.
    Raises StoreUnavailable if a store cannot be read, unless ``allow_partial``."""
    found, unavailable = _collect(db, status)
    if unavailable and not allow_partial:
        raise StoreUnavailable(unavailable[0])
    return found


def pending_summary(db=None) -> dict:
    """Read-only counts for the standing brief. Unlike the CoS nudge query this
    never writes, and an unreachable store is reported, not counted as zero."""
    summary = {"coverage": "complete", "stores": {}}
    try:
        rows = _guild_rows(
            db, "SELECT count(*) AS n, min(requested_at) AS oldest"
                " FROM guild.guest_requests WHERE status='requested'"
        )
        row = rows[0] if rows else {"n": 0, "oldest": None}
        summary["stores"][GUILD] = {"count": int(row["n"]), "oldest": _iso(row["oldest"])}
    except StoreUnavailable:
        summary["stores"][GUILD] = {"count": None, "oldest": None, "error": "unavailable"}
        summary["coverage"] = "partial"
    try:
        pending = _pending_entries()
        oldest = min((p.get("requested_at") or "" for p in pending), default=None) or None
        summary["stores"][PENDING_FILE] = {"count": len(pending), "oldest": oldest}
    except StoreUnavailable:
        summary["stores"][PENDING_FILE] = {"count": None, "oldest": None, "error": "unreadable"}
        summary["coverage"] = "partial"
    return summary


def resolve(identifier: str, db=None) -> Resolution:
    """Open requests matching a name or email exactly (case-insensitive),
    across both stores. Every match is returned; the caller clarifies when
    there is not exactly one. An unreadable store makes the resolution
    incomplete, so an apparent single match is never treated as unique."""
    wanted = identifier.strip().lower()
    if not wanted:
        return Resolution((), True)
    found, unavailable = _collect(db, "requested")
    matches = tuple(r for r in found
                    if r.name.strip().lower() == wanted or r.email.strip().lower() == wanted)
    return Resolution(matches, not unavailable, unavailable)


def get(ref: str, db=None) -> GuestRequest | None:
    """The request, or None when verifiably absent. Raises StoreUnavailable
    when its store cannot be read (absence is then unknown)."""
    store, _, key = ref.partition(":")
    if store == GUILD and key.isdigit():
        rows = _guild_rows(db, _SELECT_GUILD + " WHERE id=%s", (int(key),))
        return _from_guild(rows[0]) if rows else None
    if store == PENDING_FILE and key:
        entry = next((p for p in _pending_entries() if p.get("token") == key), None)
        return _from_pending(entry) if entry else None
    return None


def reject(ref: str, db=None) -> Outcome:
    """Reject one open request by store-qualified reference and verify it.

    Only a ``requested`` request changes. A granted request, or a registration
    whose approval already created the account, is a ``conflict``: rejecting
    it would not remove access, and revoking an account is a different action.
    ``unknown`` means the store could not be read before or after the write,
    so the effect is unverified and must not be retried blindly. The verified
    state records whether the request is still eligible for pending nudges,
    which select ``status='requested'`` only."""
    store, _, key = ref.partition(":")
    if store == GUILD and key.isdigit():
        try:
            changed, row = _db(db).reject_if_requested(int(key))
        except StoreUnavailable:
            return Outcome("unknown", ref, {"store": GUILD, "error": "unavailable"})
        except Exception:
            return Outcome("unknown", ref, {"store": GUILD, "error": "write_unconfirmed"})
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
        try:
            result = auth.pending_reject(key)
        except (auth.AuthStoreError, OSError):
            return Outcome("unknown", ref, {"store": PENDING_FILE, "error": "unreadable"})
        if result == "provisioned":
            return Outcome("conflict", ref, {"store": PENDING_FILE, "status": "provisioned",
                                             "account_active": True})
        if result == "not_found":
            return Outcome("not_found", ref)
        try:
            still_there = any(p.get("token") == key for p in _pending_entries())
        except StoreUnavailable:
            return Outcome("unknown", ref, {"store": PENDING_FILE, "error": "readback_unavailable"})
        if still_there:
            return Outcome("unknown", ref, {"store": PENDING_FILE, "error": "readback_mismatch"})
        return Outcome("done", ref, {"store": PENDING_FILE, "status": "removed", "nudge_eligible": False})
    return Outcome("invalid_ref", ref)


# ── Grant: a resumable cross-store state machine ────────────────────────────
#
# A grant touches the account file (create the guest), then Postgres (status granted), and is
# followed by notifications the caller sends. It is recorded as an intent in the auth store,
# under the same lock as account writes, and moves:
#     provisioning → account_created → granted
# A crash at any point resumes from the intent: an account is never created twice, the request
# is never marked granted unless the account really exists, and a lost temporary password is
# replaced (with must-change) rather than guessed. An existing guest with the same email is a
# conflict: the owner extends or revokes that account instead of having it silently replaced.

INTENTS_FILE = "guest_grant_intents.json"
MAX_GRANT_DAYS = 30


def _intents() -> dict:
    return auth._load_json(INTENTS_FILE, strict=True).setdefault("intents", {})


def _save_intent(ref: str, intent: dict):
    data = auth._load_json(INTENTS_FILE, strict=True)
    data.setdefault("intents", {})[ref] = intent
    auth._write_json(INTENTS_FILE, data)


def grant(ref: str, *, duration_days: int = 7, db=None, now: datetime | None = None,
          password_factory=lambda: secrets.token_urlsafe(9)) -> Outcome:
    """Grant one open website request (``guild:<id>``) after the owner approved this exact
    effect. Returns done / already_applied / conflict / not_found / unknown / invalid_ref.

    The whole grant runs under the account-store lock (a rare owner action), so concurrent or
    repeated calls serialize: exactly one creates the account and returns the one temporary
    password; the others see the finished intent and return already_applied."""
    with auth._auth_lock():
        return _grant_locked(ref, duration_days, db, now, password_factory)


def _grant_locked(ref, duration_days, db, now, password_factory) -> Outcome:
    store, _, key = ref.partition(":")
    if store != GUILD or not key.isdigit():
        return Outcome("invalid_ref", ref)
    if isinstance(duration_days, bool) or not isinstance(duration_days, int) \
            or not 1 <= duration_days <= MAX_GRANT_DAYS:
        return Outcome("conflict", ref, {"error": "duration_invalid"})
    now = now or datetime.now(timezone.utc)
    try:
        request = get(ref, db)
    except StoreUnavailable:
        return Outcome("unknown", ref, {"store": GUILD, "error": "unavailable"})
    if request is None:
        return Outcome("not_found", ref)
    secret = None
    try:
        with auth._auth_lock():
            intent = _intents().get(ref)
            if request.status != "requested" and not (intent and intent.get("state") == "account_created"):
                if request.status == "granted" and intent and intent.get("state") == "granted":
                    return Outcome("already_applied", ref, _grant_state(request, intent))
                return Outcome("conflict", ref, {"store": GUILD, "status": request.status})
            if intent is None:
                existing = [g for g in auth.load_guests()
                            if g.get("email", "").lower() == request.email.lower() and request.email]
                if existing:
                    return Outcome("conflict", ref, {"store": GUILD, "status": request.status,
                                                     "error": "guest_email_exists",
                                                     "existing_username": existing[0]["username"]})
                intent = {"state": "provisioning", "email": request.email, "name": request.name,
                          "started_at": now.isoformat(),
                          "expires_at": (now + timedelta(days=duration_days)).isoformat()}
                _save_intent(ref, intent)
            if intent["state"] == "provisioning":
                adopted = next((g for g in auth.load_guests()
                                if g.get("email", "").lower() == intent["email"].lower()
                                and g.get("created_at", "") >= intent["started_at"]), None)
                if adopted:           # crashed after creating the account: adopt it, reset its password
                    secret = password_factory()
                    auth.reset_guest_password(adopted["username"], secret)
                    username = adopted["username"]
                else:
                    secret = password_factory()
                    guest = auth.create_guest(intent["name"] or intent["email"], intent["expires_at"], secret,
                                              email=intent["email"], on_email_conflict="refuse")
                    username = guest["username"]
                auth.set_must_change_password(username)
                intent.update(state="account_created", username=username)
                _save_intent(ref, intent)
    except auth.GuestEmailConflict:
        return Outcome("conflict", ref, {"store": GUILD, "error": "guest_email_exists"})
    except (auth.AuthStoreError, OSError):
        return Outcome("unknown", ref, {"store": "auth", "error": "account_store_unavailable"})

    # The account exists; now the request status, verified by read-back.
    try:
        changed, row = _db(db).grant_if_requested(int(key))
    except Exception:
        return Outcome("unknown", ref, {"store": GUILD, "error": "write_unconfirmed",
                                        "account": "created"}, delivery_secret=secret)
    if row is None or row["status"] != "granted":
        return Outcome("unknown", ref, {"store": GUILD, "status": row and row["status"],
                                        "account": "created"}, delivery_secret=secret)
    with auth._auth_lock():
        intent["state"] = "granted"
        _save_intent(ref, intent)
    state = _grant_state(_from_guild(row), intent)
    if secret is None:
        # Resumed after the account step already returned its password: issue a fresh one so the
        # approval email never goes out without a usable credential.
        secret = password_factory()
        auth.reset_guest_password(intent["username"], secret)
        auth.set_must_change_password(intent["username"])
    return Outcome("done", ref, state, delivery_secret=secret)


def _grant_state(request: GuestRequest, intent: dict) -> dict:
    username = intent.get("username")
    return {"store": GUILD, "status": request.status, "username": username,
            "account_active": bool(username) and auth.is_guest_active(username),
            "must_change_password": bool(username) and auth.check_must_change_password(username),
            "expires_at": intent.get("expires_at"), "nudge_eligible": request.status == "requested",
            "notifications_pending": ["approval_email", "owner_notice"]}
