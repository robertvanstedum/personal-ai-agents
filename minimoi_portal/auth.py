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


def authenticate(username: str, password: str) -> tuple:
    """
    Returns (user_dict, None) on success or (None, error_message) on failure.
    user_dict always contains 'tier': 'owner' | 'family' | 'guest'
    """
    username = username.strip().lower()

    # Check permanent users first
    for user in load_users():
        if user["username"].lower() == username:
            if check_password_hash(user["password_hash"], password):
                return user, None
            return None, "Incorrect password."

    # Check guests
    for guest in load_guests():
        if guest["username"].lower() == username:
            if not check_password_hash(guest["password_hash"], password):
                return None, "Incorrect password."
            # Check expiry
            try:
                expires_at = datetime.fromisoformat(guest["expires_at"])
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > expires_at:
                    return None, "Guest access has expired."
            except (KeyError, ValueError):
                return None, "Guest access configuration error."
            return guest, None

    return None, "User not found."


def create_guest(display_name: str, expires_at_iso: str, password: str) -> dict:
    """
    Create a new guest credential. Returns the guest dict including auto-generated username.
    expires_at_iso: ISO 8601 string e.g. '2026-06-15T00:00:00Z'
    """
    data = _load_json("guests.json")
    if "guests" not in data:
        data["guests"] = []

    username = f"guest_{secrets.token_hex(4)}"
    guest = {
        "username": username,
        "password_hash": generate_password_hash(password),
        "tier": "guest",
        "display_name": display_name,
        "expires_at": expires_at_iso,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    data["guests"].append(guest)
    _write_json("guests.json", data)
    return guest


def revoke_guest(username: str) -> bool:
    """Remove a guest by username. Returns True if found and removed."""
    data = _load_json("guests.json")
    guests = data.get("guests", [])
    new_guests = [g for g in guests if g["username"] != username]
    if len(new_guests) == len(guests):
        return False
    data["guests"] = new_guests
    _write_json("guests.json", data)
    return True


def list_guests() -> list:
    """Return all guests with expiry status."""
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
