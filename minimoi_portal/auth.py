"""
minimoi_portal/auth.py — User loading, authentication, guest management.

Passwords are hashed with werkzeug (ships with Flask, no extra dep).
Generate a hash: python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('yourpassword'))"
"""

import contextlib
import fcntl
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

AUTH_DIR = Path(__file__).parent / "auth"

# One lock serializes every account-file read-modify-write: an in-process
# RLock for the portal's request threads plus an fcntl lock on a sidecar file
# for any other process. Production bind-mounts users.json and guests.json as
# single files, so writes go in place (temp-file + rename would detach the
# mount) and the lock, not atomic replacement, is what prevents lost updates.
_THREAD_LOCK = threading.RLock()
_LOCK_STATE = threading.local()


class AuthStoreError(RuntimeError):
    """An account file exists but cannot be parsed. Writers refuse to proceed
    rather than treat it as empty, which would silently wipe every account."""


class GuestEmailConflict(ValueError):
    """A guest with this email already exists and replacement was not allowed."""


@contextlib.contextmanager
def _auth_lock():
    with _THREAD_LOCK:
        depth = getattr(_LOCK_STATE, "depth", 0)
        if depth:
            _LOCK_STATE.depth = depth + 1
            try:
                yield
            finally:
                _LOCK_STATE.depth -= 1
            return
        AUTH_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(AUTH_DIR / ".auth.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            _LOCK_STATE.depth = 1
            try:
                yield
            finally:
                _LOCK_STATE.depth = 0
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _load_json(filename: str, strict: bool = False) -> dict:
    """Missing file → {}. A malformed file → {} for readers (login degrades),
    AuthStoreError when strict (every writer), so a damaged file is never
    overwritten with a near-empty one."""
    f = AUTH_DIR / filename
    with _auth_lock():
        if not f.exists():
            return {}
        try:
            data = json.loads(f.read_text())
        except Exception as exc:
            if strict:
                raise AuthStoreError(f"{filename} is unreadable; refusing to write over it") from exc
            return {}
    if not isinstance(data, dict):
        if strict:
            raise AuthStoreError(f"{filename} is not a JSON object; refusing to write over it")
        return {}
    return data


def _write_json(filename: str, data: dict) -> None:
    f = AUTH_DIR / filename
    payload = json.dumps(data, indent=2)
    with _auth_lock():
        with open(f, "w") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())


def load_users() -> list:
    return _load_json("users.json").get("users", [])


def load_guests() -> list:
    return _load_json("guests.json").get("guests", [])


def load_pending() -> list:
    return _load_json("pending.json").get("pending", [])


def authenticate(username: str, password: str) -> tuple:
    """
    Returns (user_dict, None) on success or (None, error_message) on failure.
    user_dict always contains 'tier': 'owner' | 'family' | 'guest'

    Accepts either username OR email address in the username field.
    Pending registrations get a clear waiting message.
    """
    login = username.strip().lower()

    # Check permanent users (by username or email)
    for user in load_users():
        if user["username"].lower() == login or user.get("email", "").lower() == login:
            if check_password_hash(user["password_hash"], password):
                return user, None
            return None, "Invalid email or password."

    # Check active guests (by username or email)
    for guest in load_guests():
        if guest["username"].lower() == login or guest.get("email", "").lower() == login:
            if not check_password_hash(guest["password_hash"], password):
                return None, "Invalid email or password."
            try:
                expires_at = datetime.fromisoformat(guest["expires_at"])
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > expires_at:
                    return None, "Guest access has expired."
            except (KeyError, ValueError):
                return None, "Guest access configuration error."
            return guest, None

    # Check pending — friendly waiting message
    for pending in load_pending():
        if pending["username"].lower() == login or pending.get("email", "").lower() == login:
            if check_password_hash(pending["password_hash"], password):
                return None, "Your account is pending approval. You'll receive access once approved."
            return None, "Invalid email or password."

    return None, "Invalid email or password."


def create_guest(display_name: str, expires_at_iso: str, password: str,
                 email: str = "", on_email_conflict: str = "replace") -> dict:
    """
    Create an active guest credential. Returns the guest dict.
    expires_at_iso: ISO 8601 string e.g. '2026-06-15T00:00:00Z'
    on_email_conflict: "replace" (legacy default) removes any guest with the
    same email first; "refuse" raises GuestEmailConflict instead, so a repeat
    grant cannot silently replace someone's username and password.
    """
    if on_email_conflict not in ("replace", "refuse"):
        raise ValueError("on_email_conflict must be 'replace' or 'refuse'")
    with _auth_lock():
        data = _load_json("guests.json", strict=True)
        if "guests" not in data:
            data["guests"] = []

        if email:
            same = [g for g in data["guests"]
                    if g.get("email", "").lower() == email.lower()]
            if same and on_email_conflict == "refuse":
                raise GuestEmailConflict(f"a guest with email {email!r} already exists")
            data["guests"] = [g for g in data["guests"] if g not in same]

        username = f"guest_{secrets.token_hex(4)}"
        guest = {
            "username":      username,
            "password_hash": generate_password_hash(password),
            "tier":          "guest",
            "display_name":  display_name,
            "email":         email,
            "expires_at":    expires_at_iso,
            "created_at":    datetime.now(timezone.utc).isoformat(),
        }
        data["guests"].append(guest)
        _write_json("guests.json", data)
    return guest


def create_pending(display_name: str, email: str, password: str) -> dict:
    """
    Create a pending registration. Does NOT grant login access.
    If the same email already has a pending entry, it is replaced.
    Returns the pending dict (includes token for admin approval link).
    """
    with _auth_lock():
        data = _load_json("pending.json", strict=True)
        if "pending" not in data:
            data["pending"] = []

        # Remove any previous pending entry for this email so it can be re-submitted
        if email:
            data["pending"] = [p for p in data["pending"]
                               if p.get("email", "").lower() != email.lower()]

        token    = secrets.token_hex(16)
        username = f"guest_{secrets.token_hex(4)}"
        entry = {
            "token":         token,
            "username":      username,
            "password_hash": generate_password_hash(password),
            "tier":          "guest",
            "display_name":  display_name,
            "email":         email,
            "requested_at":  datetime.now(timezone.utc).isoformat(),
        }
        data["pending"].append(entry)
        _write_json("pending.json", data)
    return entry


def approve_pending(token: str) -> dict | None:
    """
    Move a pending registration to active guests (7-day trial window).
    If the same email already exists in guests, the old entry is replaced.
    Returns the new guest dict or None if token not found.

    Progress is recorded first: the pending entry is marked ``approving`` (with
    a stable start time), then the guest is written, then the entry is removed,
    all under one lock. A crash at any point leaves a retryable entry, a retry
    keeps the same expiry, and pending_reject() can tell that the account may
    already exist instead of reporting a clean rejection.
    """
    from datetime import timedelta

    with _auth_lock():
        data = _load_json("pending.json", strict=True)
        pending_list = data.get("pending", [])

        entry = next((p for p in pending_list if p["token"] == token), None)
        if not entry:
            return None

        started = entry.get("approving", {}).get("started_at")
        if not started:
            started = datetime.now(timezone.utc).isoformat()
            entry["approving"] = {"started_at": started}
            _write_json("pending.json", data)

        # 7-day trial window from the (stable) start of this approval
        expires_at = (datetime.fromisoformat(started) + timedelta(days=7)).isoformat()

        guests_data = _load_json("guests.json", strict=True)
        if "guests" not in guests_data:
            guests_data["guests"] = []

        # Remove any existing guest entry for this email so the email can be reused
        email = entry.get("email", "")
        if email:
            guests_data["guests"] = [g for g in guests_data["guests"]
                                      if g.get("email", "").lower() != email.lower()]
        # A retry after a crash must not add the same username twice.
        guests_data["guests"] = [g for g in guests_data["guests"]
                                  if g["username"] != entry["username"]]

        guest = {
            "username":      entry["username"],
            "password_hash": entry["password_hash"],
            "tier":          "guest",
            "display_name":  entry["display_name"],
            "email":         email,
            "expires_at":    expires_at,
            "created_at":    datetime.now(timezone.utc).isoformat(),
        }
        guests_data["guests"].append(guest)
        _write_json("guests.json", guests_data)

        data["pending"] = [p for p in pending_list if p["token"] != token]
        _write_json("pending.json", data)
    return guest


def load_pending_strict() -> list:
    """Pending registrations for domain decisions: an unreadable file raises
    AuthStoreError instead of looking like an empty list (login keeps using
    the forgiving load_pending)."""
    return _load_json("pending.json", strict=True).get("pending", [])


def pending_reject(token: str) -> str:
    """Reject one pending registration. Returns "rejected", "not_found", or
    "provisioned" when an approval already created (or may have created) the
    account: the entry is then left in place, because rejecting it would not
    remove the active guest and revocation is a different action."""
    with _auth_lock():
        data = _load_json("pending.json", strict=True)
        pending_list = data.get("pending", [])
        entry = next((p for p in pending_list if p["token"] == token), None)
        if entry is None:
            return "not_found"
        guests = _load_json("guests.json", strict=True).get("guests", [])
        if any(g["username"] == entry["username"] for g in guests):
            return "provisioned"
        data["pending"] = [p for p in pending_list if p["token"] != token]
        _write_json("pending.json", data)
    return "rejected"


def reject_pending(token: str) -> bool:
    """Remove a pending registration. Returns True only if it was rejected;
    False when not found or when the account was already provisioned."""
    return pending_reject(token) == "rejected"


def revoke_guest(username: str) -> bool:
    """Remove an active guest by username. Returns True if found."""
    with _auth_lock():
        data = _load_json("guests.json", strict=True)
        guests = data.get("guests", [])
        new_guests = [g for g in guests if g["username"] != username]
        if len(new_guests) == len(guests):
            return False
        data["guests"] = new_guests
        _write_json("guests.json", data)
    return True


def is_guest_active(username: str) -> bool:
    """True if username is still a non-expired guest. False if revoked (no
    longer in guests.json) or expired — used to invalidate an existing
    session cookie on each request, since revoke_guest() only removes the
    credential and previously had no effect on a session already issued."""
    for g in load_guests():
        if g["username"] != username:
            continue
        try:
            expires_at = datetime.fromisoformat(g["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) <= expires_at
        except (KeyError, ValueError):
            return False
    return False


def extend_guest(username: str, days: int = 7) -> bool:
    """Extend a guest's expiry by N days from today. Returns True if found."""
    from datetime import timedelta
    with _auth_lock():
        data = _load_json("guests.json", strict=True)
        for g in data.get("guests", []):
            if g["username"] == username:
                base = max(datetime.now(timezone.utc),
                           datetime.fromisoformat(g["expires_at"]).replace(tzinfo=timezone.utc)
                           if datetime.fromisoformat(g["expires_at"]).tzinfo is None
                           else datetime.fromisoformat(g["expires_at"]))
                g["expires_at"] = (base + timedelta(days=days)).isoformat()
                _write_json("guests.json", data)
                return True
    return False


def reset_user_password(username: str, new_password: str) -> bool:
    """Set a new password for a permanent user. Returns True if user found and updated."""
    with _auth_lock():
        data = _load_json("users.json", strict=True)
        users = data.get("users", [])
        found = False
        for user in users:
            if user["username"] == username:
                user["password_hash"] = generate_password_hash(new_password)
                found = True
                break
        if found:
            _write_json("users.json", data)
    return found


def reset_guest_password(username: str, new_password: str) -> bool:
    """Set a new password for an active guest. Returns True if guest found and updated."""
    with _auth_lock():
        data = _load_json("guests.json", strict=True)
        guests = data.get("guests", [])
        found = False
        for guest in guests:
            if guest["username"] == username:
                guest["password_hash"] = generate_password_hash(new_password)
                found = True
                break
        if found:
            _write_json("guests.json", data)
    return found


def set_must_change_password(username: str) -> None:
    """Set must_change_password=True on a user or guest account."""
    with _auth_lock():
        for filename in ("users.json", "guests.json"):
            data = _load_json(filename, strict=True)
            key = "users" if filename == "users.json" else "guests"
            for entry in data.get(key, []):
                if entry["username"] == username:
                    entry["must_change_password"] = True
                    _write_json(filename, data)
                    return


def clear_must_change_password(username: str) -> None:
    """Clear must_change_password flag on a user or guest account."""
    with _auth_lock():
        for filename in ("users.json", "guests.json"):
            data = _load_json(filename, strict=True)
            key = "users" if filename == "users.json" else "guests"
            for entry in data.get(key, []):
                if entry["username"] == username:
                    entry.pop("must_change_password", None)
                    _write_json(filename, data)
                    return


def check_must_change_password(username: str) -> bool:
    """Return True if the account has must_change_password set."""
    for filename in ("users.json", "guests.json"):
        data = _load_json(filename)
        key = "users" if filename == "users.json" else "guests"
        for entry in data.get(key, []):
            if entry["username"] == username:
                return bool(entry.get("must_change_password"))
    return False


def create_reset_request(username: str, display_name: str, email: str) -> dict:
    """Append a password reset request. Returns the new request dict."""
    import uuid
    with _auth_lock():
        data = _load_json("reset_requests.json", strict=True)
        if "requests" not in data:
            data["requests"] = []
        req = {
            "id": str(uuid.uuid4()),
            "username": username,
            "display_name": display_name,
            "email": email,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
        data["requests"].append(req)
        _write_json("reset_requests.json", data)
    return req


def load_reset_requests() -> list:
    """Return active (non-expired) reset requests. Prunes expired on read.
    The prune write is skipped (not forced) when the file is unreadable."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    with _auth_lock():
        try:
            data = _load_json("reset_requests.json", strict=True)
        except AuthStoreError:
            return []
        active, changed = [], False
        for r in data.get("requests", []):
            try:
                requested_at = datetime.fromisoformat(r["requested_at"])
                if requested_at.tzinfo is None:
                    requested_at = requested_at.replace(tzinfo=timezone.utc)
                if requested_at > cutoff:
                    active.append(r)
                else:
                    changed = True
            except Exception:
                changed = True
        if changed:
            data["requests"] = active
            _write_json("reset_requests.json", data)
    return active


def consume_reset_request(req_id: str) -> bool:
    """Remove a reset request by id. Returns False if not found or expired."""
    with _auth_lock():
        active = load_reset_requests()
        found = next((r for r in active if r["id"] == req_id), None)
        if not found:
            return False
        data = _load_json("reset_requests.json", strict=True)
        data["requests"] = [r for r in data.get("requests", []) if r["id"] != req_id]
        _write_json("reset_requests.json", data)
    return True


def list_guests() -> list:
    """Return all active guests with expiry status."""
    now = datetime.now(timezone.utc)
    result = []
    for g in load_guests():
        try:
            expires_at = datetime.fromisoformat(g["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            expired = now > expires_at
        except (KeyError, ValueError):
            expired = True
        result.append({**g, "expired": expired})
    return result


# ── Email verification tokens ────────────────────────────────────────────────

def _prune_verifications(verifications: list) -> list:
    """Remove expired entries from a verifications list."""
    now = datetime.now(timezone.utc)
    kept = []
    for v in verifications:
        try:
            exp = datetime.fromisoformat(v["expires_at"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if now < exp:
                kept.append(v)
        except (KeyError, ValueError):
            pass
    return kept


def create_verification(email: str, name: str, reason: str, domain: str) -> str:
    """
    Store a pending email verification token (24h TTL).
    One token per email — replaces any prior unverified entry for the same address.
    Returns the token string.
    """
    from datetime import timedelta
    with _auth_lock():
        data = _load_json("email_verifications.json", strict=True)
        verifications = _prune_verifications(data.get("verifications", []))
        verifications = [v for v in verifications
                         if v.get("email", "").lower() != email.lower()]
        token = secrets.token_hex(16)
        verifications.append({
            "token":      token,
            "email":      email.lower(),
            "name":       name,
            "reason":     reason,
            "domain":     domain,
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
        })
        _write_json("email_verifications.json", {"verifications": verifications})
    return token


def consume_verification(token: str) -> dict | None:
    """
    Validate a token, remove it, and return its stored fields.
    Returns None if the token is not found or has expired. Writes only when
    something was actually removed (the public verify link is a GET).
    """
    with _auth_lock():
        data = _load_json("email_verifications.json", strict=True)
        stored = data.get("verifications", [])
        verifications = _prune_verifications(stored)
        entry = next((v for v in verifications if v.get("token") == token), None)
        if entry:
            verifications = [v for v in verifications if v.get("token") != token]
        if len(verifications) != len(stored):
            _write_json("email_verifications.json", {"verifications": verifications})
    return entry
