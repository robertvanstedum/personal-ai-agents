"""
minimoi_portal/auth.py — User loading, authentication, guest management.

Passwords are hashed with werkzeug (ships with Flask, no extra dep).
Generate a hash: python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('yourpassword'))"
"""

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

AUTH_DIR = Path(__file__).parent / "auth"


def _load_json(filename: str) -> dict:
    f = AUTH_DIR / filename
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text())
    except Exception:
        return {}


def _write_json(filename: str, data: dict) -> None:
    f = AUTH_DIR / filename
    f.write_text(json.dumps(data, indent=2))


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
            return None, "Incorrect password."

    # Check active guests (by username or email)
    for guest in load_guests():
        if guest["username"].lower() == login or guest.get("email", "").lower() == login:
            if not check_password_hash(guest["password_hash"], password):
                return None, "Incorrect password."
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
            return None, "Incorrect password."

    return None, "User not found."


def create_guest(display_name: str, expires_at_iso: str, password: str,
                 email: str = "") -> dict:
    """
    Create an active guest credential. Returns the guest dict.
    expires_at_iso: ISO 8601 string e.g. '2026-06-15T00:00:00Z'
    """
    data = _load_json("guests.json")
    if "guests" not in data:
        data["guests"] = []

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
    data = _load_json("pending.json")
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
    """
    from datetime import timedelta

    data = _load_json("pending.json")
    pending_list = data.get("pending", [])

    entry = next((p for p in pending_list if p["token"] == token), None)
    if not entry:
        return None

    # Remove from pending
    data["pending"] = [p for p in pending_list if p["token"] != token]
    _write_json("pending.json", data)

    # 7-day trial window from approval
    expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

    guests_data = _load_json("guests.json")
    if "guests" not in guests_data:
        guests_data["guests"] = []

    # Remove any existing guest entry for this email so the email can be reused
    email = entry.get("email", "")
    if email:
        guests_data["guests"] = [g for g in guests_data["guests"]
                                  if g.get("email", "").lower() != email.lower()]

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
    return guest


def reject_pending(token: str) -> bool:
    """Remove a pending registration. Returns True if found."""
    data = _load_json("pending.json")
    pending_list = data.get("pending", [])
    new_list = [p for p in pending_list if p["token"] != token]
    if len(new_list) == len(pending_list):
        return False
    data["pending"] = new_list
    _write_json("pending.json", data)
    return True


def revoke_guest(username: str) -> bool:
    """Remove an active guest by username. Returns True if found."""
    data = _load_json("guests.json")
    guests = data.get("guests", [])
    new_guests = [g for g in guests if g["username"] != username]
    if len(new_guests) == len(guests):
        return False
    data["guests"] = new_guests
    _write_json("guests.json", data)
    return True


def extend_guest(username: str, days: int = 7) -> bool:
    """Extend a guest's expiry by N days from today. Returns True if found."""
    from datetime import timedelta
    data = _load_json("guests.json")
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
    data = _load_json("users.json")
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
    data = _load_json("guests.json")
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
    for filename in ("users.json", "guests.json"):
        data = _load_json(filename)
        key = "users" if filename == "users.json" else "guests"
        for entry in data.get(key, []):
            if entry["username"] == username:
                entry["must_change_password"] = True
                _write_json(filename, data)
                return


def clear_must_change_password(username: str) -> None:
    """Clear must_change_password flag on a user or guest account."""
    for filename in ("users.json", "guests.json"):
        data = _load_json(filename)
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
    data = _load_json("reset_requests.json")
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
    """Return active (non-expired) reset requests. Prunes expired on read."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    data = _load_json("reset_requests.json")
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
    active = load_reset_requests()
    found = next((r for r in active if r["id"] == req_id), None)
    if not found:
        return False
    data = _load_json("reset_requests.json")
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
