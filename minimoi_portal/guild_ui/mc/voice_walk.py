"""The walk (spec v1.1): one realtime session whose model is MC, and a server that writes.

Decisions, recorded with the code (13 Nov 2026, while Robert tests Mein Deutsch on a walk):

- The model is given three read tools only. It does not call ``save_note`` or ``consult``.
- This host mints through the provider functions, not ``bootstrap.py``. That file's
  allowlist stays ``consult`` and ``save_note``, and German, Portuguese and CoS are untouched.
  ``MODEL_ACTIONS`` stays false. ``page_responses`` is not used, so the OpenAI 1.5 s
  page wait is not on this path. End-of-utterance silence stays 1200 ms.
- The page posts MC's finished sentence. The server holds one waiting sentence per
  session. A later committed utterance confirms it only when ``bare_confirmation``
  says it is a bare yes. A stop drops it. Extra words do not confirm.
- The stored note is MC's sentence, marked "confirmed by Robert, by voice", written
  by ``mc``. He may be told to say "Noted" only after the register returns an id.
- A sentence MC spoke as ``Task: ...`` is the same note with ``for=act``. That note
  is the accepted task. INIT-0013 is not submitted from the phone: that store needs
  a seat token and a card, and a wrong command would open work he has not seen.
  The desk is the act-note. A later turn of the full MC picks it up even if the
  phone is locked, because the note is already on disk.
- A call transcript is written at the end, topic ``voice-transcript``, and the text
  says it is not a decision. Nothing is shown during the call.
- Production compose sets ``MINIMOI_MC_VOICE_PATH=realtime`` so this deploy
  turns the talking call on. Removing that line restores the chained path.
"""
from __future__ import annotations

import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.realtime_voice.direct_host import _BARE_NO, bare_answer

READ_TOOLS = ("notes_recall", "deploy_status", "issues")


def talking_reservation(max_minutes: int) -> tuple[str, int]:
    """The allowance for one talking call: the realtime rate for its whole cap, not the transcription rate."""
    from core.realtime_voice import budget
    return budget.REALTIME_DIRECT_KIND, int(max_minutes) * budget.REALTIME_ESTIMATE_MICRO_PER_MINUTE


MARK = "confirmed by Robert, by voice"
_LOCK = threading.Lock()
_SESSIONS: dict[str, dict] = {}


def read_tools() -> list[dict]:
    """The only tools this session may carry. Reads. No write name."""
    specs = {
        "notes_recall": "Read the current notes Robert has settled. Do not invent one.",
        "deploy_status": "Read the release the delivery record says is live. Do not invent a check.",
        "issues": "Read what a named issue says in the one repository. Do not invent a status.",
    }
    return [{
        "type": "function",
        "name": name,
        "description": specs[name],
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "additionalProperties": False},
    } for name in READ_TOOLS]


def persona_excerpt(release: str | None = None, root: str | None = None) -> str:
    """The master persona in the portal tree, not the OpenClaw container's copy."""
    release = release or os.environ.get("MINIMOI_MC_PERSONA_RELEASE", "0.1.34")
    base = Path(root) if root else Path(__file__).resolve().parents[3]
    soul = base / "services" / "mc_operations" / "personas" / "mc-chat" / "releases" / release / "SOUL.md"
    if not soul.is_file():
        return "You are Master Craftsman, Robert's build partner in MiniMoi."
    text = soul.read_text(encoding="utf-8", errors="replace").strip()
    return text[:4000]


def instructions(release: str | None = None, root: str | None = None, briefing: str = "") -> str:
    """What the realtime model is. Slim. The skill catalogue is not here."""
    brief = briefing.strip() or "No briefing was loaded."
    return "\n".join((
        persona_excerpt(release, root),
        "",
        "You are MC on a walk. Speak in your own words. British English.",
        "Do not invent a check, a release, or a note. If you need a live fact, say so and use a read tool.",
        "A design talk may run long. A question that needs the code or a build is a task, not something you finish on the call.",
        "When a decision is ready, say one or two sentences and stop. Start a decision with 'Decision:'.",
        "When he assigns work, say it back in one or two sentences and stop. Start a task with 'Task:'.",
        "Do not say that you noted it. The server tells you when the register has a receipt.",
        "A fragment is not a turn. Do not act on half a sentence.",
        "",
        "Briefing (orientation, with the time it was read):",
        brief,
    ))


def open_session(session: str, principal: str) -> None:
    """Remember a session this server just issued, and who it was issued to."""
    with _LOCK:
        _SESSIONS[session] = {"principal": principal, "pending": None}


def _owned(session: str, principal: str) -> dict | None:
    with _LOCK:
        item = _SESSIONS.get(session)
        if item is None or item.get("principal") != principal:
            return None
        return item


def hold(session: str, sentence: str, principal: str) -> dict:
    """Hold one Decision or Task. Any other reply drops the hold. Unknown sessions are refused."""
    if _owned(session, principal) is None:
        return {"ok": False, "error": "unknown_session"}
    sentence = " ".join((sentence or "").split())
    if not sentence.startswith("Decision:") and not sentence.startswith("Task:"):
        with _LOCK:
            item = _SESSIONS.get(session)
            if item is not None and item.get("principal") == principal:
                item["pending"] = None
        return {"ok": True, "waiting": False, "dropped": True}
    pending = {"id": uuid.uuid4().hex, "sentence": sentence[:2000], "at": _now()}
    with _LOCK:
        item = _SESSIONS.get(session)
        if item is None or item.get("principal") != principal:
            return {"ok": False, "error": "unknown_session"}
        item["pending"] = pending
    return {"ok": True, "waiting": True, "hold": pending["id"]}


def drop(session: str) -> None:
    with _LOCK:
        item = _SESSIONS.get(session)
        if item is not None:
            item["pending"] = None


def waiting(session: str) -> dict | None:
    with _LOCK:
        item = _SESSIONS.get(session)
        pending = item.get("pending") if item else None
        return dict(pending) if pending else None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _kind(sentence: str) -> str:
    return "act" if sentence.lower().startswith("task:") else "remember"


def _topic(sentence: str) -> str:
    return "voice-task" if _kind(sentence) == "act" else "voice-decision"


def _has_stop(text: str) -> bool:
    words = re.sub(r"[^\w']+", " ", (text or "").lower().replace("\u2019", "'")).replace("_", " ").split()
    i = 0
    while i < len(words):
        for n in (3, 2, 1):
            if len(words) - i >= n and " ".join(words[i:i + n]) in _BARE_NO:
                return True
        i += 1
    return False


def confirm(session: str, utterance: str, *, hold_id: str, principal: str, notes_add) -> dict:
    """Write only a bare yes for the held sentence. A stop word never confirms."""
    if _owned(session, principal) is None:
        return {"ok": False, "error": "unknown_session"}
    text = utterance or ""
    if _has_stop(text) or bare_answer(text) == "no":
        drop(session)
        return {"ok": True, "confirmed": False, "dropped": True}
    item = waiting(session)
    if bare_answer(text) != "yes" or item is None or item.get("id") != hold_id:
        return {"ok": True, "confirmed": False, "stray": item is None}
    sentence = item["sentence"]
    try:
        note = notes_add(
            by="mc",
            said_by="mc",
            what_for=_kind(sentence),
            topic=_topic(sentence),
            text=f"{sentence}\n\n{MARK}\ncall: {item['at']}",
            ref=(session or "")[:8],
        )
    except Exception:
        return {"ok": False, "confirmed": False, "error": "not_written"}
    drop(session)
    receipt = note.get("id") if isinstance(note, dict) else None
    if not receipt:
        return {"ok": False, "confirmed": False, "error": "no_receipt"}
    return {"ok": True, "confirmed": True, "receipt": receipt, "say": "Noted.", "for": _kind(sentence)}


def transcript_note(session: str, turns: list, *, principal: str, notes_add) -> dict:
    """History at the end of the call. Labelled as a transcript, not a decision. An unknown session is refused."""
    if _owned(session, principal) is None:
        return {"ok": False, "error": "unknown_session"}
    lines = []
    for turn in turns or []:
        if not isinstance(turn, dict):
            continue
        role = "Robert" if turn.get("role") == "user" else "MC"
        said = " ".join(str(turn.get("text") or "").split())
        if said:
            lines.append(f"{role}: {said}")
    if not lines:
        return {"ok": True, "written": False}
    body = "Transcript, not a decision.\n\n" + "\n".join(lines)
    note = notes_add(by="mc", said_by="mc", what_for="remember", topic="voice-transcript", text=body[:2000], ref=(session or "")[:8])
    return {"ok": True, "written": True, "receipt": note.get("id")}


def run_read(name: str, query: str, *, readers: dict) -> dict:
    """A read tool. Unknown names do nothing. Readers return plain dicts."""
    if name not in READ_TOOLS:
        return {"ok": False, "error": "not_a_read"}
    fn = readers.get(name)
    if fn is None:
        return {"ok": True, "found": False, "text": "No record is wired for that read."}
    try:
        return {"ok": True, **fn(query)}
    except Exception:
        return {"ok": False, "error": "read_failed"}
