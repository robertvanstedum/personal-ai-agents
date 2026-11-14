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


def call_record_folder(services, environ=None) -> str | None:
    """Where a call line is written.

    ``MC_TURNS_DIR`` when it names an existing directory (production mounts
    ``/app/data/mc-turns`` there). Otherwise the Guild store folder, and only
    when that store has a path. A missing folder writes nothing.
    """
    env = os.environ if environ is None else environ
    turns = str(env.get("MC_TURNS_DIR", "") or "").strip()
    if turns and os.path.isdir(turns):
        return turns
    store = getattr(services, "store", None)
    if store is None or not getattr(store, "path", None):
        return None
    folder = getattr(store, "folder", None)
    return folder if isinstance(folder, str) and folder else None


def voice_calls_of(services, environ=None) -> VoiceCallLog:
    """The call log, or one that writes nothing when no folder exists."""
    return VoiceCallLog(call_record_folder(services, environ))


__all__ = ["VoiceCallLog", "voice_calls_of", "FILE_NAME"]
