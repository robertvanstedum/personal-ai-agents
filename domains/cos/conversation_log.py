"""CoS Confer's own conversation record, so CoS can recall past conversations (CoS gets MC's skills, 26 Oct 2026).

One JSON line per Confer turn in ``<COS_CONVERSATIONS_DIR>/<YYYY-MM>.jsonl``: when, the conversation, Robert's own
words, the names of what he attached (never the file text, which stays in the inbox), CoS's reply and the downloads
the platform delivered. cos-scheduler writes it; CoS's runtime reads it read-only and searches it like any file.
Off unless COS_CONVERSATIONS_DIR names an existing directory. A failed write never fails the turn.

When the canonical CoS history is on (domains/cos/history.py, spec v0.3 section 4.19), this log is its projection:
each line is written after the history has the answer, and carries ``"projection": true`` so the "Before 1.2" import
never takes it back in.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger("cos.conversation_log")
_lock = threading.Lock()
MAX_REPLY_CHARS = 20_000


def folder(environ) -> Path | None:
    value = environ.get("COS_CONVERSATIONS_DIR") or ""
    return Path(value) if value and os.path.isabs(value) and os.path.isdir(value) else None


def append(root: Path | None, *, conversation_id: str, turn_id: str, channel: str, owner_text: str,
           attachments: list, reply: str, files: list, projection: bool = False) -> None:
    if root is None:
        return
    now = datetime.now(timezone.utc)
    line = {"at": now.isoformat(timespec="seconds"), "conversation_id": conversation_id, "turn_id": turn_id,
            "channel": channel, "robert": owner_text, "attachments": attachments,
            "cos": reply[:MAX_REPLY_CHARS], "downloads": [f.get("path") for f in files or []]}
    if projection:
        line["projection"] = True
    try:
        with _lock:
            fd = os.open(root / f"{now:%Y-%m}.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        _log.warning("cos conversation log: turn %s not recorded", turn_id, exc_info=True)


def system_note(agent_dir: str) -> str:
    return (f"Recall: every earlier Confer turn with Robert is in {agent_dir}/<YYYY-MM>.jsonl (one JSON line per turn: "
            "at, robert, attachments, cos, downloads). When Robert refers to an earlier conversation, or the answer "
            "depends on what he told you before, search those files first (grep) and say which turn you used, "
            "rather than asking again.")
