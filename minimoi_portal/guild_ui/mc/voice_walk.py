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
- The whole call is saved at the end as one file, like a Mein Deutsch session: every
  turn, no length cap, in MC's turns folder (#578). It is not a note and not a decision.
  Nothing is shown during the call.
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


SESSIONS_DIR = "voice-sessions"


def _call_path(folder: str, session: str, when: datetime) -> Path:
    """``<folder>/voice-sessions/<year>/<date>_<session>.json``: one file per call, a year folder as for MC's turns."""
    safe = re.sub(r"[^0-9a-f]", "", (session or "").lower())[:32] or "unknown"
    return Path(folder) / SESSIONS_DIR / when.strftime("%Y") / f"{when.strftime('%Y-%m-%d')}_{safe}.json"


def _find_call(folder: str, session: str) -> Path | None:
    safe = re.sub(r"[^0-9a-f]", "", (session or "").lower())[:32]
    if len(safe) < 8:
        return None
    found = sorted((Path(folder) / SESSIONS_DIR).glob(f"*/*_{safe}.json"))
    return found[0] if found else None


def open_call(session: str, principal: str, *, folder: str | None, now: datetime | None = None) -> bool:
    """At the start of a call: its file, with its owner and no turns yet. End can then be checked against the file
    even after a portal restart has emptied the in-memory session table."""
    if not folder:
        return False
    when = now or datetime.now(timezone.utc)
    record = {"schema_version": 1, "record_type": "mc_voice_call", "agent": "mc", "path": "realtime",
              "session_id": session, "principal": principal, "date": when.strftime("%Y-%m-%d"),
              "started_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "label": "Transcript of a voice call, not decisions.",
              "turn_count": 0, "raw_transcript": []}
    from core.durable_files import write_doc
    try:
        write_doc(_call_path(folder, session, when), record)
        return True
    except OSError:
        return False


def save_call(session: str, turns: list, *, principal: str, folder: str | None, now: datetime | None = None) -> dict:
    """The whole call as one file, the way Mein Deutsch keeps a session (``sessions/<date>_<nnn>.json``).

    Every turn, in order, with no length cap: this is the record of what was said, not a note and not a decision.
    Written atomically into MC's turns folder (``MC_TURNS_DIR``; production sets it in the 1.2 layer). The caller must
    own the call: the live session table, or after a restart the owner the call's file was opened with. An unknown
    call or a missing folder writes nothing and says so."""
    if not folder:
        return {"ok": False, "error": "no_folder"}
    from core.durable_files import read_doc, write_doc
    existing = _find_call(folder, session)
    opened = read_doc(existing) if existing else None
    if _owned(session, principal) is None and not (isinstance(opened, dict) and opened.get("principal") == principal):
        return {"ok": False, "error": "unknown_session"}
    kept = []
    for turn in turns or []:
        if not isinstance(turn, dict):
            continue
        said = " ".join(str(turn.get("text") or "").split())
        if said:
            kept.append({"role": "robert" if turn.get("role") == "user" else "mc", "text": said})
    if not kept:
        return {"ok": True, "written": False}
    when = now or datetime.now(timezone.utc)
    path = existing or _call_path(folder, session, when)
    record = dict(opened) if isinstance(opened, dict) else {
        "schema_version": 1, "record_type": "mc_voice_call", "agent": "mc", "path": "realtime",
        "session_id": session, "principal": principal, "date": when.strftime("%Y-%m-%d"),
        "label": "Transcript of a voice call, not decisions."}
    record.update({"saved_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "turn_count": len(kept), "raw_transcript": kept})
    try:
        write_doc(path, record)
    except OSError as exc:
        return {"ok": False, "error": f"not_written ({type(exc).__name__})"}
    return {"ok": True, "written": True, "turns": len(kept), "file": path.name}


def call_text(session: str, *, principal: str, folder: str | None) -> str | None:
    """The saved call as plain text for its owner, or None. Reads the file, so it works after a restart. The full
    session id only (32 hex characters), so a short id never matches by suffix."""
    if not folder or not re.fullmatch(r"[0-9a-f]{32}", (session or "").lower()):
        return None
    from core.durable_files import read_doc
    path = _find_call(folder, session)
    record = read_doc(path) if path else None
    if not isinstance(record, dict) or record.get("principal") != principal or not record.get("raw_transcript"):
        return None
    lines = [f"MC voice call, {record.get('saved_at', '')} (UTC). {record.get('label', '')}", ""]
    for turn in record.get("raw_transcript") or []:
        who = "Robert" if turn.get("role") == "robert" else "MC"
        lines.append(f"{who}: {turn.get('text', '')}")
    return "\n".join(lines) + "\n"


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
