"""Voice turns stay hidden while the session is live, then appear once (Spec 164 v0.3 §6, §8).

One store: the conversation's floor notes, where mc_voice_transcripts.retain keeps voice turns. A direct-path session
writes a start marker at bootstrap and an end marker at End (platform notes, page 'voice-session', never shown as
messages). Its records are its transcript notes (author managed_voice_transcript:<session>), its consult notes
(vc-<session hex>-<owner item>, from the consult's key; vc-<session hex>-<n> before Spec 165's fix round) and MC's kept
replies to them (api._reply_key of those ids), its summary proposal and decision.

Every other note written while a consult runs (a platform receipt or reply: owner-action, board, register, library,
tool-proposal or decision) is tagged with the consult's session as it is written (mc_voice_direct._TaggedFloor): a
tag note (page 'voice-session', never shown) names the note's request id, and the process also holds the tag in
memory until the tag note is read. Such a note is that session's record here, hidden and revealed with it.

The state is read from those markers and the clock (direct_host.state), so it holds across tabs, refreshes and a portal
restart:
- ended (end marker): revealed;
- dropped (no end marker, maximum duration passed): revealed, on the next load;
- live (open in this process and heard from within LIVE_WINDOW_S): hidden;
- recovering (otherwise): hidden, and the conversation shows RECOVERY instead of a gap.
Reveal is a view of durable records, so it happens once: nothing is copied or re-sent, and a revealed session cannot
be hidden again. A session with no start marker (the managed path's) is shown as before.

The host-agnostic half (the state from markers, end intent, receipt lines, what is spoken of a reply, the recovery and
summary words) is core.realtime_voice.direct_host's, imported here; this module is the Guild floor store and its view.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid

from core.realtime_voice.direct_host import (LIVE_WINDOW_S, NOT_ACTED_RECORD, PLAYBACK, PLAYBACK_RECORD, RECOVERY,  # noqa: F401
                                             SUMMARY_PROMPT, SUMMARY_UNAVAILABLE, Put, Receipts, affirmative, done_intent,
                                             full_differs, now, parse_stamp, same_record, session_id, stamp,
                                             state as _state)

PAGE_SESSION = "voice-session"
PAGE_SUMMARY = "voice-summary"
SESSION_AUTHOR = "voice_session:"
SUMMARY_AUTHOR = "voice_summary:"
MARKER = "[Voice session] "
RECORD = "[Voice record] "         # a durable record of the session (Spec 165 §4.4): a consult's request and outcome
CONSULT_CAP = 12                   # core.realtime_voice.consult.CAP: a session has at most this many consult notes
# A consult note's id: vc-<session hex>-<owner item> (the consult's key, Spec 165 §5.1), or vc-<session hex>-<n> (A1).
_CONSULT_ID = re.compile(r"vc-([0-9a-f]{32})-([0-9]{2}|t[0-9]{1,4})")


def key(sid, what):
    return str(uuid.uuid5(uuid.UUID(sid), "voice-session:" + what))


def consult_note_id(sid, turn):
    """The note a consult keeps the owner's words as, and MC's typed-turn request id: derived from the consult's key
    (Spec 165 §5.1), the session and the owner turn's capture id ("t5"), so a second dispatch of the same owner turn is
    the same note and MC's one reply per note holds too. An int ``turn`` is A1's place-in-session form, still read."""
    return f"vc-{uuid.UUID(sid).hex}-{turn:02d}" if isinstance(turn, int) else f"vc-{uuid.UUID(sid).hex}-{turn}"


def reply_id(note_id):
    """MC's kept reply to a note (api._reply_key, without importing the routes)."""
    return "mc-" + hashlib.sha256(note_id.encode()).hexdigest()[:40]


def consult_reply_ids(sid):
    """MC's kept replies to this session's A1-form consult notes (vc-<hex>-01 … -12). Replies to key-form notes are
    found from the notes themselves (view) or known by the turn that writes them (_TaggedFloor)."""
    return {reply_id(consult_note_id(sid, n)): n for n in range(1, CONSULT_CAP + 1)}


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


def tag_for_turn(floor, owner_turn, request_id):
    """Tag a note written outside a consult's own request (an MC tool callback's notice, a job's later result) with the
    voice session whose consult note is ``owner_turn`` (vc-<session>-<owner item>, or A1's -<n>), before it is written.
    Returns the session,
    or None when the owner turn is not a voice consult (a typed turn: nothing is tagged)."""
    match = _CONSULT_ID.fullmatch(str(owner_turn or ""))
    if match is None or not isinstance(request_id, str):
        return None
    sid = str(uuid.UUID(match.group(1)))
    mark_tag(floor, sid, request_id)
    return sid


def _tag_of(row):
    """(tagged request id, session) for a tag note, else None."""
    sid = session_of(row)
    fields = _marker_fields(row, sid, "tag") if sid is not None else None
    rid = (fields or {}).get("request_id")
    return (rid, sid) if isinstance(rid, str) and row.get("request_id") == tag_key(rid) else None


def record_key(sid, rid):
    return str(uuid.uuid5(uuid.UUID(sid), "voice-record:" + rid))


def put_record(floor, sid, rec):
    """Keep one durable record (Spec 165 §4.4) as a session note (page voice-session, never shown): a Put. The note's
    request id is the record's, so the floor's own idempotency is the first-write-wins rule; a different record under it
    is a conflict, unless only its time differs (same_record). The floor commits before it answers."""
    text = RECORD + json.dumps(rec, sort_keys=True, separators=(",", ":"))
    kept = floor.add_note(record_key(sid, rec["id"]), text, _session_author(sid), page=PAGE_SESSION)
    if kept.outcome == "kept" and kept.value:
        return Put("duplicate" if kept.repeated else "stored")
    if kept.outcome == "idempotency_mismatch":
        return Put("duplicate" if same_record(get_record(floor, sid, rec["id"]), rec) else "conflict")
    return Put("refused", str(kept.outcome))


def get_record(floor, sid, rid):
    """The durable record ``rid`` of this session, or None."""
    row = floor.get_note(record_key(sid, rid))
    if (not isinstance(row, dict) or row.get("author_kind") != "platform" or row.get("who") != SESSION_AUTHOR + sid
            or (row.get("context") or {}).get("page") != PAGE_SESSION or not isinstance(row.get("text"), str)
            or not row["text"].startswith(RECORD)):
        return None
    try:
        found = json.loads(row["text"][len(RECORD):])
    except ValueError:
        return None
    return found if isinstance(found, dict) and found.get("id") == rid else None


def mark_start(floor, sid, *, principal, conversation_id, max_minutes, started=None):
    return _write_marker(floor, sid, "start", {"principal": principal, "conversation_id": conversation_id,
                                              "max_minutes": int(max_minutes), "started_at": stamp(started or now())})


def mark_end(floor, sid, *, reason="end", ended=None, capture=None):
    """``capture`` (Spec 165 §5.2): {"capture": "complete" | "incomplete", "missing": n, "boundary": last_item_id}, kept
    in the marker for every later view and the summary. A marker without it (A1's, when complete) reads as complete."""
    fields = {k: v for k, v in (capture or {}).items() if k in ("capture", "missing", "boundary")}
    return _write_marker(floor, sid, "end", {"reason": reason, "ended_at": stamp(ended or now()), **fields})


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
    return _state(start, end, sid, live=live, when=when), start, end


class Hider:
    """Per-request: each session's state read once. An unreadable start marker hides a session's records only when it
    is known to be a direct-path session (``direct(sid)``: held in this process, or a direct-only record of it is in
    view); a managed-path transcript, which has no start marker, is shown as before (review B #9)."""

    def __init__(self, floor, *, live=lambda sid: False, when=None, direct=lambda sid: False):
        self.floor, self.live, self.when, self.direct, self.states = floor, live, when or now(), direct, {}

    def __call__(self, sid):
        if sid not in self.states:
            try:
                self.states[sid] = state(self.floor, sid, live=self.live, when=self.when)
            except Exception:
                self.states[sid] = ("unknown", None, None) if self.direct(sid) else (None, None, None)
        return self.states[sid]

    def hidden(self, sid):
        return self(sid)[0] in ("live", "recovering", "unknown")


def _direct_only(row, sid):
    """A record only the direct path writes (a marker, tag or summary note, or a consult note), so its session is a
    direct-path one whatever its markers read."""
    who = row.get("who") if isinstance(row, dict) else None
    if row.get("author_kind") == "platform" and who in (SESSION_AUTHOR + sid, SUMMARY_AUTHOR + sid):
        return True
    match = _CONSULT_ID.fullmatch(str(row.get("request_id") or ""))
    return bool(match) and str(uuid.UUID(match.group(1))) == sid


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
        kind = {"owner ASR": "owner_turn", "assistant generated; playback unverified": "assistant_turn",
                "consult request": "consult_request", "consult receipt": "consult_receipt"}[match["speaker"]]
        text = row["text"][match.end():]
        shown = {"text": text, "partial": match["partial"] == "true", "interrupted": match["interrupted"] == "true",
                 "started_at": match["start"], "ended_at": match["end"], "part": int(match["part"]),
                 "parts": int(match["total"])}
        if kind == "consult_receipt":
            shown.update(receipt_parts(text))
        return {**out, "kind": kind, **shown}
    if page == PAGE_SUMMARY:
        return {**out, "kind": {key(sid, "summary"): "summary_proposal", key(sid, "summary-decision"): "summary_decision",
                                key(sid, "summary-unavailable"): "summary_unavailable"}.get(row.get("request_id"), "summary")}
    if row.get("request_id") in replies:
        return {**out, "kind": "consult_reply", "full_differs": full_differs(row.get("text"))}
    if _CONSULT_ID.fullmatch(str(row.get("request_id") or "")):
        return {**out, "kind": "consult_note"}
    return {**out, "kind": "consult_outcome"}                     # a tagged platform note written during a consult


# MC's receipt words (direct_host.Receipts): a consult's receipt line ("Answered. Reply mc-…. Spoken: …") as the core
# writes it for this host, and read back here. The record keeps it whole; the thread shows "Checked with MC · <outcome>"
# and the spoken words, with the reply, operation or job id kept out of the visible text (UI review D1: machine detail in
# Robert's thread). A spoken job command (jobs_api.job_command) is an action the platform took, not a failure.
RECEIPTS = Receipts(("reply", "operation", "job"), acted_key="job_command", acted_kind="job",
                    acted={"run_job": ("Job started", "job started"),
                           "delegate": ("Delegated job started", "delegated job started"),
                           "stop": ("Job stop requested", "job stop requested")},
                    acted_other=("Job command", "job command sent"))
receipt_parts = RECEIPTS.parts


def _capture(status, end):
    """What a later view says of capture (Spec 165 §5.2): incomplete with its count from the end marker; unconfirmed for a
    session whose End never arrived (v0.3, CX V2-4, G4: dropped, or still recovering on this or another device), never
    complete; else nothing (complete)."""
    end = end or {}
    if end.get("capture") == "incomplete":
        return {"capture": "incomplete", **({"missing": end["missing"]} if type(end.get("missing")) is int else {})}
    if end.get("capture") == "unconfirmed" or (not end and status in ("dropped", "recovering", "unknown")):
        return {"capture": "unconfirmed"}
    return {}


def _playback(floor, sid):
    """How the session's spoken summary ended (Spec 165 v0.3 §5.3, G3), from its durable record: {summary_playback,
    summary_heard} (heard only when drained), or {} when none was kept or it cannot be read."""
    try:
        rec = get_record(floor, sid, PLAYBACK_RECORD)
    except Exception:
        return {}
    if not isinstance(rec, dict) or rec.get("outcome") not in PLAYBACK:
        return {}
    return {"summary_playback": rec["outcome"], "summary_heard": rec["outcome"] == "drained" and rec.get("heard") is True}


def _not_acted(floor, sid):
    """The note ids of this session's owner turns the page named as not acted on (review B S3, N2), from its durable
    record: the first part of each such turn, which carries its label. Two sets: those not acted on (refused or
    overtaken), and those whose reply End cut off (why "closed", Gate 2 Q N12: he ended it, so they read "Cut off at End").
    Empty when none was kept or it cannot be read."""
    try:
        rec = get_record(floor, sid, NOT_ACTED_RECORD)
    except Exception:
        return set(), set()
    items = rec.get("owner_item_ids") if isinstance(rec, dict) else None
    why = rec.get("why") if isinstance(rec, dict) and isinstance(rec.get("why"), dict) else {}
    items = [item for item in items if isinstance(item, str)] if isinstance(items, list) else []
    return ({transcript_id(sid, "user", item) for item in items if why.get(item) != "closed"},
            {transcript_id(sid, "user", item) for item in items if why.get(item) == "closed"})


def process_view(config):
    """view's keywords from a mount's config: what this process (mc_voice_direct.install) knows of its sessions."""
    return {"live": config.get("mc_direct_voice_live") or (lambda _sid: False),
            "tagged": config.get("mc_direct_voice_tagged") or (lambda _rid: None),
            "known": config.get("mc_direct_voice_known") or (lambda _sid: False),
            "running": config.get("mc_direct_voice_running") or (lambda _sid, _item: False)}


def _record_row(row, sid):
    """A durable record note of this session (put_record), parsed, or None."""
    if (not isinstance(row, dict) or row.get("who") != SESSION_AUTHOR + sid or row.get("author_kind") != "platform"
            or (row.get("context") or {}).get("page") != PAGE_SESSION or not str(row.get("text") or "").startswith(RECORD)):
        return None
    try:
        found = json.loads(row["text"][len(RECORD):])
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def transcript_id(sid, role, item, part=0):
    """A voice record's note id, as mc_voice_transcripts.retain keeps it (core transcripts.note_parts)."""
    return str(uuid.uuid5(uuid.UUID(sid), f"transcript:{role}:{item}:0:{part}"))


def _unresolved(floor, rows, sid, running):
    """{consult request note id: what to show} for this session's admitted consults that have no receipt: the process
    that ran one died mid-turn (result unknown until reconciled), or it is still running here (Spec 165 §5.1)."""
    ids = {row.get("request_id") for row in rows}
    out = {}
    for row in rows:
        rec = _record_row(row, sid)
        item = (rec or {}).get("owner_item_id")
        if (rec or {}).get("kind") != "consult_request" or not isinstance(item, str):
            continue
        receipt = transcript_id(sid, "receipt", "k" + item)
        if receipt in ids or floor.get_note(receipt) is not None:
            continue
        out[transcript_id(sid, "consult", "k" + item)] = "still running" if running(sid, item) else "result unknown"
    return out


def view(floor, rows, *, live=lambda sid: False, when=None, tagged=lambda request_id: None, known=lambda sid: False,
         running=lambda sid, item: False):
    """(rows to show, the direct-path sessions in this window). Markers are never shown; a live or recovering
    session's records are withheld; a revealed session's records carry ``voice`` {session_id, kind, …} (_voice).
    ``tagged(request_id)``: the session this process tagged a note with, before its tag note can be read.
    ``known(sid)``: a direct-path session this process holds (it stays hidden if its markers cannot be read).
    ``running(sid, item)``: an owner turn's consult still running in this process. A revealed consult with no receipt
    is shown as one ("Checked with MC · result unknown", or "still running"), never as nothing."""
    if not isinstance(rows, list) or not rows:
        return rows, []
    tags = dict(found for found in map(_tag_of, rows) if found is not None)
    evidence = set(tags.values())
    hider = Hider(floor, live=live, when=when, direct=lambda sid: known(sid) or sid in evidence)

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
        if sid is not None and _direct_only(row, sid):
            evidence.add(sid)
    direct = [sid for sid in sids if hider(sid)[0] is not None]
    replies = {}
    for sid in direct:
        replies.update({reply: sid for reply in consult_reply_ids(sid)})
    for row in rows:                                   # replies to key-form consult notes, from the notes in view
        match = _CONSULT_ID.fullmatch(str(row.get("request_id") or ""))
        sid = str(uuid.UUID(match.group(1))) if match else None
        if sid in direct:
            replies[reply_id(row["request_id"])] = sid
    unresolved, not_acted, cut_off = {}, set(), set()
    for sid in direct:
        if not hider.hidden(sid):
            try:
                unresolved.update(_unresolved(floor, rows, sid, running))
            except Exception:                           # a floor that cannot be read: the request stays as it is
                pass
            named, ended = _not_acted(floor, sid)
            not_acted |= named
            cut_off |= ended
    shown = []
    for row in rows:
        sid = owner(row) or replies.get(row.get("request_id"))
        if sid is None or sid not in direct:
            shown.append(row)
            continue
        if (row.get("context") or {}).get("page") == PAGE_SESSION or hider.hidden(sid):
            continue
        voice = _voice(row, replies, sid)
        if voice.get("kind") == "consult_request" and row.get("request_id") in unresolved:
            voice = {**voice, "kind": "consult_receipt", "outcome": unresolved[row["request_id"]], "unresolved": True,
                     "spoken": "", "ref": None, "ref_kind": None}
        if voice.get("kind") == "owner_turn" and row.get("request_id") in not_acted:
            voice = {**voice, "not_acted": True}           # his words that were not acted on: say it again if needed
        elif voice.get("kind") == "owner_turn" and row.get("request_id") in cut_off:
            voice = {**voice, "cut_off": True}             # his words whose reply End cut off (Q N12)
        shown.append({**row, "voice": voice})
    sessions = []
    for sid in direct:
        status, start, end = hider(sid)
        summary = summary_state(floor, sid) if status in ("ended", "dropped") else None
        sessions.append({"session_id": sid, "state": status,
                         "started_at": (start or {}).get("started_at"), "ended_at": (end or {}).get("ended_at"),
                         "line": RECOVERY if status in ("recovering", "unknown") else None,
                         "summary": summary,
                         # How its spoken summary ended, and whether it was heard (only when drained; v0.3 §5.3, G3).
                         **(_playback(floor, sid) if summary in ("proposed", "confirmed", "edited", "declined") else {}),
                         # Some of what was said is known not to be kept (review B4): every later view says so, with
                         # the count when there is one (Spec 165 §5.2). A session whose End never came may not have all
                         # of it: unconfirmed, said too (review R2 N6; v0.3 G4).
                         **_capture(status, end)})
    return shown, sessions


# ── §8: the summary proposal (done_intent and affirmative are direct_host's) ──────────────────────────────────────

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

