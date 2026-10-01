"""CoS Private mode (Spec 160 §2, D6): sticky, server-owned, per conversation id.

The mode lives in ``COS_TURNS_DIR/_mode.json``, keyed by conversation id; web
text, voice and (later) Telegram all use ``owner``::

    {"owner": {"mode": "private", "changed_at": "2026-09-29T21:04:00Z", "revision": 3}}

Reading:
- no turn log (``COS_TURNS_DIR`` unset): nothing is kept anywhere, and the
  switch is unavailable;
- no ``_mode.json``: kept (the default is to log);
- a file that cannot be read or parsed, or a mode this reader does not know:
  **Private**.

``epoch`` is a short digest of the file's bytes ("absent" when there is none).
A voice session carries the epoch it started with; if it differs at Stop, the
mode changed during the session and the session counts as Private (F2).

Writing is owner-only (the portal's ``X-Minimoi-User-Tier: owner``), JSON only,
small, and atomic: a temporary file (0600) renamed over ``_mode.json`` under an
exclusive lock. Private only ever turns off by an explicit switch.

Telegram ``/private`` and ``/public`` are a follow-up; they will write the same
file through ``set_private``.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request

from core.identity import resolve_user_id

CONVERSATION_ID = "owner"
MODE_FILE = "_mode.json"
MAX_BODY = 1024
DISCLOSURE = "Private: not kept in your CoS history. The agent itself may still remember it."


def turns_dir() -> Path | None:
    value = os.environ.get("COS_TURNS_DIR", "").strip()
    return Path(value) if value else None


def _private_from(modes, conversation_id: str) -> bool:
    if not isinstance(modes, dict):
        return True
    mode = modes.get(conversation_id)
    if mode is None:
        return False
    if isinstance(mode, dict):
        mode = mode.get("mode", "private" if mode.get("private") else "public")
    if mode in ("public", "kept"):
        return False
    return True  # "private", or anything this reader does not recognise


def read_mode(root: Path, conversation_id: str = CONVERSATION_ID) -> tuple[bool, str]:
    """(private, epoch) for conversation_id."""
    path = root / MODE_FILE
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return False, "absent"
    except OSError:
        return True, "unreadable"
    epoch = hashlib.sha256(raw).hexdigest()[:16]
    try:
        modes = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return True, epoch
    return _private_from(modes, conversation_id), epoch


def is_private(root: Path, conversation_id: str = CONVERSATION_ID) -> bool:
    return read_mode(root, conversation_id)[0]


def state(conversation_id: str = CONVERSATION_ID) -> dict:
    """What the page and a voice bootstrap need: never the file's content."""
    root = turns_dir()
    if root is None:
        return {"available": False, "private": False, "epoch": None}
    private, epoch = read_mode(root, conversation_id)
    return {"available": True, "private": private, "epoch": epoch}


def set_private(root: Path, private: bool, conversation_id: str = CONVERSATION_ID, *, now=None) -> dict:
    """Write the mode for conversation_id, keeping other conversations'."""
    now = now or datetime.now(timezone.utc)
    root.mkdir(mode=0o700, exist_ok=True)
    lock_fd = os.open(root / "_mode.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            modes = json.loads((root / MODE_FILE).read_text(encoding="utf-8"))
            if not isinstance(modes, dict):
                modes = {}
        except FileNotFoundError:
            modes = {}
        except ValueError:
            modes = {}                     # an unparseable file is replaced by an explicit choice
        previous = modes.get(conversation_id)
        revision = previous.get("revision", 0) if isinstance(previous, dict) else 0
        modes[conversation_id] = {
            "mode": "private" if private else "public",
            "changed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            # Every switch changes the file, even two in one second, so a
            # voice session always sees that the mode changed under it.
            "revision": (revision if isinstance(revision, int) else 0) + 1,
        }
        tmp = root / f"{MODE_FILE}.tmp-{os.getpid()}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, (json.dumps(modes, indent=2, sort_keys=True) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, root / MODE_FILE)
    finally:
        os.close(lock_fd)
    private_now, epoch = read_mode(root, conversation_id)
    return {"available": True, "private": private_now, "epoch": epoch}


def _is_owner() -> bool:
    return (resolve_user_id(request) is not None
            and request.headers.get("X-Minimoi-User-Tier", "").strip().lower() == "owner")


def create_private_mode_blueprint() -> Blueprint:
    bp = Blueprint("cos_private_mode", __name__)

    @bp.route("/ui/private-mode", methods=["GET"])
    def get_mode():
        if resolve_user_id(request) is None:
            return jsonify({"error": "identity required"}), 401
        current = state()
        return jsonify({"available": current["available"], "private": current["private"],
                        "disclosure": DISCLOSURE})

    @bp.route("/ui/private-mode", methods=["POST"])
    def post_mode():
        if not _is_owner():
            return jsonify({"error": "owner only"}), 403
        if request.content_length is None or request.content_length > MAX_BODY:
            return jsonify({"error": "body too large or without a length"}), 413
        if not request.is_json:
            return jsonify({"error": "JSON only"}), 415
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("private"), bool):
            return jsonify({"error": "private must be true or false"}), 400
        root = turns_dir()
        if root is None:
            return jsonify({"available": False, "private": False,
                            "error": "CoS history is not kept here, so there is nothing to make private"}), 409
        try:
            set_private(root, body["private"])
        except OSError as exc:
            print(f"[cos_private_mode] saved=false error={type(exc).__name__}", flush=True)
            current = state()
            return jsonify({"available": True, "private": current["private"], "error": "mode not saved"}), 500
        current = state()
        print(f"[cos_private_mode] private={str(current['private']).lower()}", flush=True)
        return jsonify({"available": True, "private": current["private"], "disclosure": DISCLOSURE})

    return bp


__all__ = ["create_private_mode_blueprint", "read_mode", "is_private", "set_private", "state",
           "turns_dir", "DISCLOSURE", "CONVERSATION_ID"]
