"""
minimoi_portal/guest_data.py — Storage for guest interactions.

Completely separate from Robert's curator_preferences.json.
Stores likes, dislikes, saves, comments per guest user.
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent / "guest_data"
FEEDBACK_FILE = DATA_DIR / "feedback.json"
COMMENTS_FILE = DATA_DIR / "comments.json"

_lock = threading.Lock()


def _ensure_dir():
    DATA_DIR.mkdir(exist_ok=True)


def _load(filepath: Path) -> dict:
    if not filepath.exists():
        return {}
    try:
        return json.loads(filepath.read_text())
    except Exception:
        return {}


def _save(filepath: Path, data: dict) -> None:
    _ensure_dir()
    filepath.write_text(json.dumps(data, indent=2))


def record_feedback(username: str, hash_id: str, action: str) -> dict:
    """
    Record like/dislike/save for a guest user.
    Like/dislike toggle (click same action again to remove).
    Save toggles on/off.
    Returns updated state dict for the article.
    """
    with _lock:
        data = _load(FEEDBACK_FILE)
        if username not in data:
            data[username] = {}

        state = data[username].get(hash_id, {})

        if action in ("like", "dislike"):
            if state.get("reaction") == action:
                # Same button again — remove reaction
                state.pop("reaction", None)
                state.pop("reacted_at", None)
            else:
                state["reaction"] = action
                state["reacted_at"] = datetime.now(timezone.utc).isoformat()

        elif action == "save":
            if state.get("saved"):
                state["saved"] = False
            else:
                state["saved"] = True
                state["saved_at"] = datetime.now(timezone.utc).isoformat()

        data[username][hash_id] = state
        _save(FEEDBACK_FILE, data)
        return state


def get_user_feedback(username: str) -> dict:
    """Return all feedback for a user, keyed by hash_id."""
    return _load(FEEDBACK_FILE).get(username, {})


def add_comment(username: str, display_name: str, hash_id: str, text: str) -> dict:
    """Append a comment from a guest user to an article."""
    with _lock:
        data = _load(COMMENTS_FILE)
        if hash_id not in data:
            data[hash_id] = []
        comment = {
            "username": username,
            "display_name": display_name,
            "text": text.strip(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        data[hash_id].append(comment)
        _save(COMMENTS_FILE, data)
        return comment
