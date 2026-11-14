"""One line per MC voice call: when, which path, which provider and which kind of device (#570, 13 Nov 2026).

The portal configures no logging, so an INFO line is dropped on production; this file is the durable record. It sits
next to ``mc_turns.jsonl`` (the Guild data folder) as ``mc_voice_calls.jsonl``, appended, 0600. A line holds no words,
no user agent and no session secret: the time, the path ("chained" or "direct"), the provider, the device class
(core.realtime_voice.devices) and, for the direct path, the first eight characters of the session id.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone

FILE_NAME = "mc_voice_calls.jsonl"
_LOCK = threading.Lock()
_log = logging.getLogger("guild_ui.mc")


class VoiceCallLog:
    def __init__(self, folder: str | None):
        self.path = os.path.join(folder, FILE_NAME) if folder else None

    def record(self, *, path: str, provider: str | None, device: str, session: str | None = None) -> bool:
        """Append one line; False (and a warning) when it could not be written. Never raises."""
        if not self.path:
            return False
        line = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "path": path,
                "provider": provider if isinstance(provider, str) else None, "device": device}
        if isinstance(session, str) and session:
            line["session"] = session[:8]
        try:
            with _LOCK:
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as f:
                    f.write(json.dumps(line, sort_keys=True) + "\n")
            return True
        except OSError as exc:
            _log.warning("mc voice: call record not written (%s)", type(exc).__name__)
            return False


def voice_calls_of(services) -> VoiceCallLog:
    """The call log in the same folder as MC's turn trace (mc/turn_log.turn_log_of), or one that writes nothing."""
    store = getattr(services, "store", None)
    return VoiceCallLog(getattr(store, "folder", None) if store is not None and getattr(store, "path", None) else None)


__all__ = ["VoiceCallLog", "voice_calls_of", "FILE_NAME"]
