"""Voice turns stay hidden while the session is live, then appear once (Spec 164 v0.3 §6, §8).

One store: the conversation's floor notes, where mc_voice_transcripts.retain keeps voice turns. A direct-path session
writes a start marker at bootstrap and an end marker at End (platform notes, page 'voice-session', never shown as
messages). Its records are its transcript notes (author managed_voice_transcript:<session>), its consult notes
(vc-<session hex>-<n>) and MC's kept replies to them (api._reply_key of those ids), its summary proposal and decision.

Every other note written while a consult runs (a platform receipt or reply: owner-action, board, register, library,
tool-proposal or decision) is tagged with the consult's session as it is written (mc_voice_direct._TaggedFloor): a
tag note (page 'voice-session', never shown) names the note's request id, and the process also holds the tag in
memory until the tag note is read. Such a note is that session's record here, hidden and revealed with it.

The state is read from those markers and the clock, so it holds across tabs, refreshes and a portal restart:
- ended (end marker): revealed;
- dropped (no end marker, maximum duration passed): revealed, on the next load;
- live (open in this process and heard from within LIVE_WINDOW_S): hidden;
- recovering (otherwise): hidden, and the conversation shows RECOVERY instead of a gap.
Reveal is a view of durable records, so it happens once: nothing is copied or re-sent, and a revealed session cannot
be hidden again. A session with no start marker (the managed path's) is shown as before.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

RECOVERY = "Voice session ended unexpectedly; recovering what was said."
SUMMARY_PROMPT = "Shall I sum up?"
SUMMARY_UNAVAILABLE = "Summary unavailable."
PAGE_SESSION = "voice-session"
PAGE_SUMMARY = "voice-summary"
SESSION_AUTHOR = "voice_session:"
SUMMARY_AUTHOR = "voice_summary:"
MARKER = "[Voice session] "
LIVE_WINDOW_S = 60                 # the page posts a heartbeat every 20 s while connected
CONSULT_CAP = 12                   # core.realtime_voice.consult.CAP: a session has at most this many consult notes
_CONSULT_ID = re.compile(r"vc-([0-9a-f]{32})-([0-9]{2})")


def now():
    return datetime.now(timezone.utc)


def stamp(when):
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_stamp(text):
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        try:
            return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None


def session_id(value):
    """The canonical session id (a UUID string), or None."""
    try:
        canonical = str(uuid.UUID(value))
    except (TypeError, ValueError, AttributeError):
        return None
    return canonical if canonical == value else None


def key(sid, what):
    return str(uuid.uuid5(uuid.UUID(sid), "voice-session:" + what))


def consult_note_id(sid, n):
    return f"vc-{uuid.UUID(sid).hex}-{n:02d}"


def consult_reply_ids(sid):
    """MC's kept replies to this session's consult notes (api._reply_key, without importing the routes)."""
    return {"mc-" + hashlib.sha256(consult_note_id(sid, n).encode()).hexdigest()[:40]: n
            for n in range(1, CONSULT_CAP + 1)}


def _session_author(sid):
    from .stores import Author
    return Author(SESSION_AUTHOR + sid, "platform", "Voice session")


def _write_marker(floor, sid, what, fields):
    """Keep one marker. The first write of a marker wins: a second End keeps the first end time."""
    text = MARKER + json.dumps({"event": what, "session_id": sid, **fields}, sort_keys=True)
    kept = floor.add_note(key(sid, what), text, _session_author(sid), page=PAGE_SESSION)
    if kept.outcome == "kept" and kept.value:
        return _marker_fields(kept.value, sid, what)
    if kept.outcome == "idempotency_mismatch":
        return read_marker(floor, sid, what)
    return None


_TAG_NS = uuid.UUID("5c0f4f3e-6d1c-4b8e-9a51-3f1d2a7c9e40")       # tag keys: one per tagged request id, any session


def tag_key(request_id):
    return str(uuid.uuid5(_TAG_NS, "voice-tag:" + request_id))


def mark_tag(floor, sid, request_id):
    """Tag one note as written during this session's consult; the first tag of a request id wins."""
    text = MARKER + json.dumps({"event": "tag", "session_id": sid, "request_id": request_id}, sort_keys=True)
    kept = floor.add_note(tag_key(request_id), text, _session_author(sid), page=PAGE_SESSION)
    return kept.outcome == "kept" and bool(kept.value)


def _tag_of(row):
    """(tagged request id, session) for a tag note, else None."""
    sid = session_of(row)
    fields = _marker_fields(row, sid, "tag") if sid is not None else None
    rid = (fields or {}).get("request_id")
    return (rid, sid) if isinstance(rid, str) and row.get("request_id") == tag_key(rid) else None


def mark_start(floor, sid, *, principal, conversation_id, max_minutes, started=None):
    return _write_marker(floor, sid, "start", {"principal": principal, "conversation_id": conversation_id,
                                              "max_minutes": int(max_minutes), "started_at": stamp(started or now())})


def mark_end(floor, sid, *, reason="end", ended=None):
    return _write_marker(floor, sid, "end", {"reason": reason, "ended_at": stamp(ended or now())})


def _marker_fields(row, sid, what):
    if (not isinstance(row, dict) or row.get("author_kind") != "platform" or row.get("who") != SESSION_AUTHOR + sid
            or (row.get("context") or {}).get("page") != PAGE_SESSION or not isinstance(row.get("text"), str)
            or not row["text"].startswith(MARKER)):
        return None
    try:
        fields = json.loads(row["text"][len(MARKER):])
    except ValueError:
        return None
    if not isinstance(fields, dict) or fields.get("event") != what or fields.get("session_id") != sid:
        return None
    return fields


def read_marker(floor, sid, what):
    return _marker_fields(floor.get_note(key(sid, what)), sid, what)


def session_of(row):
    """The direct-path session a floor note belongs to, or None."""
    who = row.get("who") if isinstance(row, dict) else None
    if isinstance(who, str):
        from .mc_voice_transcripts import AUTHOR_PREFIX
        for prefix in (AUTHOR_PREFIX, SESSION_AUTHOR, SUMMARY_AUTHOR):
            if who.startswith(prefix) and row.get("author_kind") == "platform":
                return session_id(who[len(prefix):])
    match = _CONSULT_ID.fullmatch(str(row.get("request_id") or "")) if isinstance(row, dict) else None
    return str(uuid.UUID(match.group(1))) if match else None


def state(floor, sid, *, live=lambda sid: False, when=None):
    """(state, start fields, end fields): None for a session with no start marker (not the direct path)."""
    start = read_marker(floor, sid, "start")
    if start is None:
        return None, None, None
    end = read_marker(floor, sid, "end")
    if end is not None:
        return "ended", start, end
    began = parse_stamp(start.get("started_at"))
    limit = start.get("max_minutes")
    if began is None or type(limit) is not int:
        return "dropped", start, None                    # an unreadable start is never hidden for ever
    if (when or now()) >= began + timedelta(minutes=limit):
        return "dropped", start, None
    return ("live" if live(sid) else "recovering"), start, None


class Hider:
    """Per-request: each session's state read once. Fail closed: an unreadable marker hides the session's records."""

    def __init__(self, floor, *, live=lambda sid: False, when=None):
        self.floor, self.live, self.when, self.known = floor, live, when or now(), {}

    def __call__(self, sid):
        if sid not in self.known:
            try:
                self.known[sid] = state(self.floor, sid, live=self.live, when=self.when)
            except Exception:
                self.known[sid] = ("unknown", None, None)
        return self.known[sid]

    def hidden(self, sid):
        return self(sid)[0] in ("live", "recovering", "unknown")


def _voice(row, replies, sid):
    """What the page needs to show a revealed record: its kind and, for a retained turn, its parsed header (speaker,
    times, partial/interrupted, part) and its words without the header."""
    from .mc_voice_transcripts import HEADER, PAGE
    page = (row.get("context") or {}).get("page")
    out = {"session_id": sid}
    if page == PAGE:
        match = HEADER.match(row.get("text") or "")
        if match is None:
            return {**out, "kind": "voice_note"}
        return {**out, "kind": {"owner ASR": "owner_turn", "assistant generated; playback unverified": "assistant_turn",
                                "consult request": "consult_request", "consult receipt": "consult_receipt"}[match["speaker"]],
                "text": row["text"][match.end():], "partial": match["partial"] == "true",
                "interrupted": match["interrupted"] == "true", "started_at": match["start"], "ended_at": match["end"],
                "part": int(match["part"]), "parts": int(match["total"])}
    if page == PAGE_SUMMARY:
        return {**out, "kind": {key(sid, "summary"): "summary_proposal", key(sid, "summary-decision"): "summary_decision",
                                key(sid, "summary-unavailable"): "summary_unavailable"}.get(row.get("request_id"), "summary")}
    if row.get("request_id") in replies:
        return {**out, "kind": "consult_reply"}
    if _CONSULT_ID.fullmatch(str(row.get("request_id") or "")):
        return {**out, "kind": "consult_note"}
    return {**out, "kind": "consult_outcome"}                     # a tagged platform note written during a consult


def process_view(config):
    """view's keywords from a mount's config: what this process (mc_voice_direct.install) knows of its sessions."""
    return {"live": config.get("mc_direct_voice_live") or (lambda _sid: False),
            "tagged": config.get("mc_direct_voice_tagged") or (lambda _rid: None)}


def view(floor, rows, *, live=lambda sid: False, when=None, tagged=lambda request_id: None):
    """(rows to show, the direct-path sessions in this window). Markers are never shown; a live or recovering
    session's records are withheld; a revealed session's records carry ``voice`` {session_id, kind, …} (_voice).
    ``tagged(request_id)``: the session this process tagged a note with, before its tag note can be read."""
    if not isinstance(rows, list) or not rows:
        return rows, []
    hider = Hider(floor, live=live, when=when)
    tags = dict(found for found in map(_tag_of, rows) if found is not None)

    def owner(row):
        rid = row.get("request_id")
        if not isinstance(rid, str):
            return session_of(row)
        return session_of(row) or tags.get(rid) or tagged(rid)
    sids = []
    for row in rows:
        sid = owner(row)
        if sid is not None and sid not in sids:
            sids.append(sid)
    direct = [sid for sid in sids if hider(sid)[0] is not None]
    replies = {}
    for sid in direct:
        replies.update({reply: sid for reply in consult_reply_ids(sid)})
    shown = []
    for row in rows:
        sid = owner(row) or replies.get(row.get("request_id"))
        if sid is None or sid not in direct:
            shown.append(row)
            continue
        if (row.get("context") or {}).get("page") == PAGE_SESSION or hider.hidden(sid):
            continue
        shown.append({**row, "voice": _voice(row, replies, sid)})
    sessions = []
    for sid in direct:
        status, start, end = hider(sid)
        sessions.append({"session_id": sid, "state": status,
                         "started_at": (start or {}).get("started_at"), "ended_at": (end or {}).get("ended_at"),
                         "line": RECOVERY if status in ("recovering", "unknown") else None,
                         "summary": summary_state(floor, sid) if status in ("ended", "dropped") else None})
    return shown, sessions


# ── §8: the spoken end and the summary proposal ──────────────────────────────────────────────────────────────────
# "We're done" proposes ending; a negated, hedged or questioned one does not; a yes to the proposal ends.
_DONE = re.compile(r"\b(?:we(?:'re|’re| are) (?:all )?(?:done|finished)|(?:i'?m|i’m|i am) (?:all )?done|"
                   r"that(?:'s|’s| is) (?:all|it) for (?:now|today)|let(?:'s|’s| us) (?:stop|end|wrap up)\b)", re.I)
_HEDGED = re.compile(r"\b(?:not|never|nearly|almost|yet|if|when|until|unless|before)\b|n['’]t\b|\?\s*\Z", re.I)
_YES = re.compile(r"\A\W*(?:yes|yeah|yep|sure|please|go ahead|do it|sum (?:it )?up|ok(?:ay)?|ja|genau)\b", re.I)
_NO = re.compile(r"\b(?:no|not|don'?t|don’t|nein|wait)\b", re.I)


def done_intent(text):
    """True only for a plain spoken end ("we're done", "that's all for today"), clause by clause."""
    if not isinstance(text, str):
        return False
    return any(_DONE.search(clause) and not _HEDGED.search(clause) for clause in re.split(r"(?<=[.!;])\s+|,\s*", text))


def affirmative(text):
    return isinstance(text, str) and bool(_YES.match(text)) and not _NO.search(text)


def summary_state(floor, sid):
    """none | proposed | confirmed | edited | declined | unavailable (a proposal wins over an earlier failure)."""
    try:
        decision = _summary_row(floor.get_note(key(sid, "summary-decision")), sid)
        if decision is not None:
            return decision.get("decision") or "proposed"
        if _summary_row(floor.get_note(key(sid, "summary")), sid) is not None:
            return "proposed"
        if _summary_row(floor.get_note(key(sid, "summary-unavailable")), sid) is not None:
            return "unavailable"
    except Exception:
        return "unknown"
    return "none"


def _summary_row(row, sid):
    if (not isinstance(row, dict) or row.get("author_kind") != "platform" or row.get("who") != SUMMARY_AUTHOR + sid
            or (row.get("context") or {}).get("page") != PAGE_SUMMARY):
        return None
    head = (row.get("text") or "").split("\n", 1)[0]
    for word in ("confirmed", "edited", "declined"):
        if head.startswith(f"[Voice summary {word}"):
            return {**row, "decision": word}
    return row


def summary_author(sid):
    from .stores import Author
    return Author(SUMMARY_AUTHOR + sid, "platform", "Voice summary")


def records(floor, sid, *, max_pages=10):
    """This session's retained records only, oldest first: [(speaker, text, partial, interrupted)], and whether the
    whole session was read (its start marker was reached)."""
    from .mc_voice_transcripts import AUTHOR_PREFIX, HEADER, PAGE
    found, before, complete, start = [], None, False, key(sid, "start")
    for _ in range(max_pages):
        result = floor.list_notes(before=before, limit=200)
        rows = result.data.get("notes") if result.ok else None
        if not isinstance(rows, list):
            break
        for row in reversed(rows):
            if row.get("request_id") == start:
                complete = True
                break
            if (row.get("who") == AUTHOR_PREFIX + sid and row.get("author_kind") == "platform"
                    and (row.get("context") or {}).get("page") == PAGE):
                match = HEADER.match(row.get("text") or "")
                if match:
                    found.append((match["speaker"], row["text"][match.end():], match["partial"] == "true",
                                  match["interrupted"] == "true"))
        if complete or not rows or not result.data.get("more"):
            complete = complete or not result.data.get("more")
            break
        before = rows[0]["id"]
    return list(reversed(found)), complete

