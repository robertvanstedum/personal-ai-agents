"""Public CoS operations for already-authorized channel adapters.

Callers enforce their transport's owner check before invoking these operations.
The canonical Confer turn service retains its own validation and operation rules.
Imports are lazy: loading this interface never starts the runtime or reads memory.
"""


def chat(text: str, *, channel: str) -> str:
    """Return the owner-conversation reply, preserving errors and effects.

    F2 D4: with COS_TELEGRAM_CHAT_URL and COS_TELEGRAM_SIGNING_KEY_FILE set, cos-bot no longer runs CoS in its own
    process: it posts the turn to cos-scheduler /chat signed as service:telegram with its own key, so the turn gets a
    binding there like every other turn. Unset, the earlier in-process path is unchanged."""
    import os
    url = os.environ.get("COS_TELEGRAM_CHAT_URL", "").strip()
    if url:
        return _signed_chat(url, text, channel)
    from .chief_of_staff import _chat

    return _chat(text, channel=channel)


def _signed_chat(url: str, text: str, channel: str) -> str:
    import os
    from urllib.parse import urlsplit

    import requests

    from core import portal_assertion as PA
    key = PA.load_key(os.environ.get("COS_TELEGRAM_SIGNING_KEY_FILE"))
    env = os.environ.get("COS_TELEGRAM_SIGNING_ENV", "").strip()
    if key is None or not env:
        raise RuntimeError("Telegram to CoS signing is not configured; nothing was sent.")
    parts = urlsplit(url)
    if parts.path != "/chat" or parts.query or parts.scheme not in ("http", "https"):
        raise RuntimeError("COS_TELEGRAM_CHAT_URL must be the cos-scheduler /chat URL.")
    body = {"text": text, "channel": channel, "conversation_id": "owner"}
    prepared = requests.Request("POST", url, json=body).prepare()
    header = PA.sign(key, audience="cos-owner", subject="service:telegram", method="POST",
                     target=PA.canonical_target(parts.path, parts.query), body=prepared.body or b"", env=env)
    session = requests.Session()
    session.trust_env = False
    prepared.headers["X-Portal-Owner-Assertion"] = header
    response = session.send(prepared, timeout=(5, 300), allow_redirects=False)
    try:
        reply = response.json().get("reply")
    except ValueError:
        reply = None
    if not isinstance(reply, str):
        raise RuntimeError(f"CoS returned HTTP {response.status_code} without a reply.")
    return reply


def memory_character_count() -> int:
    """Return the existing status measure without exposing memory or mutable state."""
    from .chief_of_staff import _read_memory

    return len(_read_memory())
