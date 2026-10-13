"""The ledger (MC front door, slice 1). One folder per request under a root:

    <root>/requests/<request_id>/request.json     immutable: who asked whom for what
    <root>/requests/<request_id>/events.jsonl     append-only: delivery, receipt, progress, result, needs_you, cancel
    <root>/pending/<recipient>.log                one line per new request: a WAKE HINT only (the request file is the truth)
    <root>/pending/<recipient>.armed              touched by the recipient's watcher: "a session is listening", model-free

Rules (design 2026-10-08 section 7, kept): delivery is at least once with deduplication; a receipt means a recipient session picked the request up, it does
not mean the work was done; a result is the worker's report, not verification; nothing here approves anything; nothing here runs a model or the network.
Who wrote an event is the writer's CLAIM (``actor``); the ledger does not prove identity. Text is data and is bounded and stripped of control characters.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone

ACTORS = ("mc", "claude-code", "codex", "robert")
RECIPIENTS = ("claude-code", "codex")
EVENT_KINDS = ("delivery", "receipt", "progress", "result", "needs_you", "cancel")
STAGES = ("queued", "acknowledged", "working", "returned")          # the Workshop Log's request stages; set by receipts here, not by hand
ACTION_MAX = 4000
TEXT_MAX = 16 * 1024
LINE_MAX = 32 * 1024
LIST_MAX = 400

_RID = re.compile(r"^r-[0-9a-f]{20,40}$")
_CID = re.compile(r"^(shop-floor-thread|c-[0-9a-f]{8,32})$")
_TID = re.compile(r"^t-[0-9a-f]{8,32}$")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class Refused(ValueError):
    """A request or event the ledger will not take. ``code`` is a short machine word; the message is plain."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(value, limit: int, what: str, *, multiline: bool) -> str:
    if not isinstance(value, str):
        raise Refused("invalid", f"The {what} must be text.")
    out = _CTRL.sub("", value.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if not multiline:
        out = re.sub(r"\s+", " ", out)
    if not out:
        raise Refused("invalid", f"The {what} is empty.")
    if len(out) > limit:
        raise Refused("too_long", f"The {what} is at most {limit:,} characters.")
    return out


def valid_request_id(value) -> bool:
    return isinstance(value, str) and bool(_RID.fullmatch(value))


def valid_conversation(value) -> bool:
    return isinstance(value, str) and bool(_CID.fullmatch(value))


def request_id_for(seed: str) -> str:
    """A stable request id for one origin (the portal seeds it with the kept note's id), so a retry cannot make a second request."""
    return "r-" + hashlib.sha256(("agent-request:" + seed).encode("utf-8")).hexdigest()[:24]


def _dirs(root: str, rid: str | None = None) -> str:
    return os.path.join(root, "requests", rid) if rid else os.path.join(root, "requests")


def _mkdir(path: str) -> None:
    if os.path.islink(path):
        raise Refused("unsafe", "The ledger folder is a link; nothing was written.")
    os.makedirs(path, mode=0o700, exist_ok=True)
    if os.path.islink(path) or not os.path.isdir(path):
        raise Refused("unsafe", "The ledger folder is not a folder; nothing was written.")


def _write_new(path: str, data: bytes) -> bool:
    """Create ``path`` with ``data`` exactly once (temp file, then link); False when it already exists. Never follows a link."""
    tmp = f"{path}.{secrets.token_hex(4)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            return False
        return True
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _append_line(path: str, line: str) -> None:
    """One whole line appended under an exclusive lock, so concurrent writers cannot interleave. No link is followed."""
    if len(line) > LINE_MAX:
        raise Refused("too_long", "That entry is too long for one ledger line.")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, (line + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_json(path: str):
    try:
        if os.path.islink(path):
            return None
        with open(path, "rb") as fh:
            return json.loads(fh.read(LINE_MAX * 2).decode("utf-8"))
    except (OSError, ValueError):
        return None


def _events(root: str, rid: str) -> list[dict]:
    path = os.path.join(_dirs(root, rid), "events.jsonl")
    out: list[dict] = []
    try:
        if os.path.islink(path):
            return out
        with open(path, "rb") as fh:
            for raw in fh.read(8 * 1024 * 1024).splitlines():
                try:
                    ev = json.loads(raw.decode("utf-8"))
                except ValueError:
                    continue                                  # a damaged line is skipped, never guessed at
                if isinstance(ev, dict) and ev.get("kind") in EVENT_KINDS:
                    out.append(ev)
    except OSError:
        pass
    return out


def notice(request: dict) -> str:
    """The one-line wake notice (design section 12: validated tokens only; the request's own words are read with ``show``, as data)."""
    return (f"REQUEST {request['request_id']} from {request['from']} to {request['to']} · agent-authored, not a message or approval from Robert"
            f" · read it with the helper: scripts/agent_requests.py show {request['request_id']}")


def _digest(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def reconcile_request(root: str, request_id: str, *, frm: str, to: str, action: str, conversation_id: str | None = None, topic_id: str | None = None,
                      origin: str = "agent") -> str:
    """After a ``create_request`` that raised, what is the truth on disk? ``recorded`` (the file is there with EXACTLY this content, so the work can run), ``absent`` (the request
    file does not exist: ONLY a missing file counts as absent), ``different`` (that id holds other content), or ``unknown`` (the file exists or may exist but cannot be read or
    understood: a permission or I/O failure, damaged JSON). It never guesses "not recorded" from an unreadable file."""
    if not valid_request_id(request_id):
        return "absent"
    folder = _dirs(root, request_id)
    path = os.path.join(folder, "request.json")
    try:
        if os.path.islink(folder) or os.path.islink(path):
            return "unknown"
        with open(path, "rb") as fh:
            raw = fh.read(LINE_MAX * 2 + 1)
    except FileNotFoundError:
        try:
            os.stat(folder)                                                       # the request folder itself must be readable to say the file is truly missing
        except FileNotFoundError:
            return "absent"
        except OSError:
            return "unknown"
        return "absent"
    except OSError:
        return "unknown"
    try:
        have = json.loads(raw[: LINE_MAX * 2].decode("utf-8")) if len(raw) <= LINE_MAX * 2 else None
    except ValueError:
        have = None
    if not isinstance(have, dict) or have.get("request_id") != request_id:
        return "unknown"
    try:
        body = {"v": 1, "request_id": request_id, "from": frm, "to": to, "action": _text(action, ACTION_MAX, "request", multiline=True), "conversation_id": conversation_id,
                "topic_id": topic_id, "pointer": None, "origin": origin}
    except Refused:
        return "different"
    return "recorded" if have.get("content_sha256") == _digest(body) else "different"


def create_request(root: str, *, request_id: str, frm: str, to: str, action: str, conversation_id: str | None = None,
                   topic_id: str | None = None, pointer: dict | None = None, origin: str = "agent") -> tuple[dict, bool]:
    """(request, created). The same id with the same content is ``created=False`` and changes nothing; the same id with different content is refused."""
    if not valid_request_id(request_id):
        raise Refused("invalid", "Bad request id.")
    if frm not in ACTORS:
        raise Refused("invalid", "Unknown sender.")
    if to not in RECIPIENTS:
        raise Refused("invalid", "A request can be addressed to claude-code or codex.")
    if frm == to:
        raise Refused("invalid", "A request cannot be addressed to its sender.")
    if conversation_id is not None and not valid_conversation(conversation_id):
        raise Refused("invalid", "Bad conversation id.")
    if topic_id is not None and not _TID.fullmatch(topic_id):
        raise Refused("invalid", "Bad topic id.")
    if origin not in ("agent", "owner_message_via_mc", "owner_handoff_from_room"):
        raise Refused("invalid", "Unknown origin.")
    ptr = None
    if pointer is not None:
        if not isinstance(pointer, dict) or set(pointer) - {"path", "sha256"} or not isinstance(pointer.get("path"), str) \
                or len(pointer["path"]) > 400 or _CTRL.search(pointer["path"]) or (pointer.get("sha256") and not _SHA.fullmatch(str(pointer["sha256"]))):
            raise Refused("invalid", "Bad pointer.")
        ptr = {"path": pointer["path"], **({"sha256": pointer["sha256"]} if pointer.get("sha256") else {})}
    body = {"v": 1, "request_id": request_id, "from": frm, "to": to, "action": _text(action, ACTION_MAX, "request", multiline=True),
            "conversation_id": conversation_id, "topic_id": topic_id, "pointer": ptr, "origin": origin}
    digest = _digest(body)
    request = {**body, "created_at": _now(), "content_sha256": digest}
    folder = _dirs(root, request_id)
    _mkdir(_dirs(root))
    _mkdir(folder)
    path = os.path.join(folder, "request.json")
    if not _write_new(path, json.dumps(request, ensure_ascii=False, sort_keys=True).encode("utf-8")):
        have = _read_json(path)
        if not have or have.get("content_sha256") != digest:
            raise Refused("id_reused", "That request id already holds a different request. Nothing was changed.")
        if not any(e.get("kind") == "delivery" for e in _events(root, request_id)):          # a previous call wrote the request and failed before its first event: finish that now
            _append_line(os.path.join(folder, "events.jsonl"), json.dumps(
                {"event_id": secrets.token_hex(8), "kind": "delivery", "actor": "ledger", "at": _now(), "state": "queued", "adapter": "pending_file"}))
        return have, False
    _append_line(os.path.join(folder, "events.jsonl"), json.dumps(
        {"event_id": secrets.token_hex(8), "kind": "delivery", "actor": "ledger", "at": _now(), "state": "queued", "adapter": "pending_file"}))
    _mkdir(os.path.join(root, "pending"))
    _append_line(os.path.join(root, "pending", f"{to}.log"), notice(request))        # the hint is written last: the request is already durable
    return request, True


def _request(root: str, rid: str) -> dict | None:
    if not valid_request_id(rid):
        return None
    doc = _read_json(os.path.join(_dirs(root, rid), "request.json"))
    return doc if isinstance(doc, dict) and doc.get("request_id") == rid else None


def _derive(request: dict, events: list[dict]) -> dict:
    stage, closed, waiting = "queued", None, None
    for ev in events:
        k = ev["kind"]
        if k == "receipt" and stage == "queued":
            stage = "acknowledged"
        elif k == "progress" and stage in ("queued", "acknowledged", "working"):
            stage = "working"
        elif k == "result":
            stage, closed = "returned", "result"
        elif k == "needs_you":
            waiting = ev
        elif k == "cancel":
            closed = "cancelled"
    return {"stage": stage, "closed": closed, "needs_you": waiting if closed is None else None}


def load(root: str, rid: str) -> dict | None:
    request = _request(root, rid)
    if request is None:
        return None
    events = _events(root, rid)
    return {"request": request, "events": events, **_derive(request, events)}


@contextlib.contextmanager
def _request_lock(root: str, rid: str):
    """One writer at a time per request for a read-check-append decision (``once`` rules, an implied receipt): without it two simultaneous writers could both see "no receipt"."""
    folder = _dirs(root, rid)
    fd = os.open(os.path.join(folder, ".lock"), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _event(root: str, rid: str, actor: str, kind: str, extra: dict, *, once: bool = False) -> tuple[dict, bool]:
    if _request(root, rid) is None:
        raise Refused("not_found", "There is no such request.")
    with _request_lock(root, rid):
        return _event_unlocked(root, rid, actor, kind, extra, once=once)


def _event_unlocked(root: str, rid: str, actor: str, kind: str, extra: dict, *, once: bool = False) -> tuple[dict, bool]:
    request = _request(root, rid)
    if request is None:
        raise Refused("not_found", "There is no such request.")
    if actor not in ACTORS:
        raise Refused("invalid", "Unknown actor.")
    events = _events(root, rid)
    if any(e["kind"] == "cancel" for e in events) and kind != "cancel":
        raise Refused("cancelled", "That request was cancelled. Nothing was added.")
    if once:
        for e in events:
            if e["kind"] == kind and e.get("actor") == actor:
                if e.get("content_sha256") == extra.get("content_sha256"):
                    return e, False                                   # the same report again: one entry, no change
                raise Refused("exists", f"This {kind.replace('_', ' ')} is already recorded. Nothing was changed.")
    ev = {"event_id": secrets.token_hex(8), "kind": kind, "actor": actor, "at": _now(), **extra}
    _append_line(os.path.join(_dirs(root, rid), "events.jsonl"), json.dumps(ev, ensure_ascii=False))
    return ev, True


def receipt(root: str, rid: str, actor: str, *, session_ref: str = "") -> tuple[dict, bool]:
    """The recipient session says it picked the request up. Only the addressed recipient can receipt; once per recipient."""
    request = _request(root, rid)
    if request is not None and actor != request["to"]:
        raise Refused("not_recipient", "Only the agent the request was addressed to can acknowledge it.")
    _guard_owner(root, actor, session_ref)
    ref = _text(session_ref, 200, "session reference", multiline=False) if session_ref else ""
    return _event(root, rid, actor, "receipt", {"native_ref": ref, "content_sha256": hashlib.sha256(b"receipt").hexdigest()}, once=True)


def _report(root: str, rid: str, actor: str, kind: str, text: str, *, once: bool, recipient_only: bool = True, session_ref: str = "") -> tuple[dict, bool]:
    request = _request(root, rid)
    if request is not None and recipient_only and actor != request["to"]:
        raise Refused("not_recipient", "Only the agent the request was addressed to can report on it.")
    _guard_owner(root, actor, session_ref)
    clean = _text(text, TEXT_MAX, "report", multiline=True)
    if request is None:
        return _event(root, rid, actor, kind, {"text": clean}, once=once)                 # raises not_found
    with _request_lock(root, rid):                                                     # the implied receipt and the report are ONE decision: never two receipts, whatever races
        have = _events(root, rid)
        if not any(e["kind"] == "receipt" and e.get("actor") == actor for e in have) and not any(e["kind"] == "cancel" for e in have):
            _event_unlocked(root, rid, actor, "receipt", {"native_ref": _text(session_ref, 200, "session reference", multiline=False) if session_ref else "", "implied": True,
                                                          "content_sha256": hashlib.sha256(b"receipt").hexdigest()}, once=True)   # a report proves pickup: recorded as implied, once
        return _event_unlocked(root, rid, actor, kind, {"text": clean, "content_sha256": hashlib.sha256(clean.encode("utf-8")).hexdigest()}, once=once)


def progress(root: str, rid: str, actor: str, text: str, *, session_ref: str = ""):
    return _report(root, rid, actor, "progress", text, once=False, session_ref=session_ref)


def result(root: str, rid: str, actor: str, text: str, *, session_ref: str = ""):
    """The worker's report. It is not a verification and not an approval. One result per recipient."""
    return _report(root, rid, actor, "result", text, once=True, session_ref=session_ref)


def needs_you(root: str, rid: str, actor: str, text: str, *, session_ref: str = ""):
    """A question or blocker for the owner. It stays visible until a result or a cancel closes the request."""
    return _report(root, rid, actor, "needs_you", text, once=False, session_ref=session_ref)


def cancel(root: str, rid: str, actor: str):
    """The sender (or the owner) cancels an unanswered request. It cannot undo work already done."""
    request = _request(root, rid)
    if request is not None and actor not in (request["from"], "robert"):
        raise Refused("not_sender", "Only the sender or the owner can cancel a request.")
    return _event(root, rid, actor, "cancel", {"content_sha256": hashlib.sha256(b"cancel").hexdigest()}, once=True)


DELIVERY_STATES = ("accepted", "refused", "failed", "unknown", "needs_attention")


def record_delivery(root: str, rid: str, *, adapter: str, state: str, attempt_id: str, native_ref: str = "", reason: str = "") -> dict:
    """The connection manager's record of a transport attempt. ``accepted`` means the transport took the notice; it is NOT a receipt and changes no stage
    (only the agent's own receipt does). ``unknown`` means it may or may not have been taken: the manager never resends it blindly."""
    if state not in DELIVERY_STATES:
        raise Refused("invalid", "Unknown delivery state.")
    if _request(root, rid) is None:
        raise Refused("not_found", "There is no such request.")
    ev = {"event_id": secrets.token_hex(8), "kind": "delivery", "actor": "connection", "at": _now(), "state": state, "adapter": _text(adapter, 60, "adapter", multiline=False),
          "attempt_id": _text(attempt_id, 60, "attempt id", multiline=False), "native_ref": _text(native_ref, 200, "reference", multiline=False) if native_ref else "",
          "reason": _text(reason, 200, "reason", multiline=False) if reason else ""}
    _append_line(os.path.join(_dirs(root, rid), "events.jsonl"), json.dumps(ev, ensure_ascii=False))
    return ev


TERMINAL_DELIVERY = ("needs_attention", "failed", "refused")
REASON_MAX = 80


def terminal_failure(doc: dict, teammate: str):
    """The reason the CURRENT attempt of this request ended without an answer, or None. A delivery record of needs_attention, failed or refused is terminal only while nothing
    newer has happened: a later accepted/unknown attempt, or any receipt, progress or result from the teammate, supersedes it (a stale failure never fails a live turn)."""
    terminal = None
    for e in doc.get("events") or []:
        kind = e.get("kind")
        if kind == "delivery":
            terminal = e if e.get("state") in TERMINAL_DELIVERY else None
        elif kind in ("receipt", "progress", "result", "needs-you", "needs_you") and e.get("actor") == teammate:
            terminal = None
    if terminal is None:
        return None
    return (terminal.get("reason") or terminal.get("state") or "agent_failed")[:REASON_MAX]


def _all_ids(root: str) -> list[str]:
    try:
        names = [n for n in os.listdir(_dirs(root)) if _RID.fullmatch(n)]
    except OSError:
        return []
    names.sort(key=lambda n: _mtime(os.path.join(_dirs(root, n), "request.json")), reverse=True)
    return names[:LIST_MAX]


def _mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def _order(root: str, doc: dict) -> tuple:
    """Oldest first, with a stable tie-break for requests made in the same second: the request file's own creation time, to the nanosecond."""
    try:
        ns = os.stat(os.path.join(_dirs(root, doc["request"]["request_id"]), "request.json")).st_mtime_ns
    except OSError:
        ns = 0
    return (doc["request"]["created_at"], ns)


def pending(root: str, to: str) -> list[dict]:
    """Requests addressed to ``to`` that it has not acknowledged and that are not closed, oldest first."""
    out = []
    for rid in _all_ids(root):
        doc = load(root, rid)
        if doc and doc["request"]["to"] == to and doc["closed"] is None and doc["stage"] == "queued":
            out.append(doc)
    out.sort(key=lambda d: _order(root, d))
    return out


def for_conversation(root: str, conversation_id: str) -> list[dict]:
    """Every request tied to a conversation, oldest first (the portal reads this to show waiting, picked up, and results)."""
    if not valid_conversation(conversation_id):
        return []
    out = []
    for rid in _all_ids(root):
        doc = load(root, rid)
        if doc and doc["request"].get("conversation_id") == conversation_id:
            out.append(doc)
    out.sort(key=lambda d: _order(root, d))
    return out


def connection_owner(root: str, agent: str) -> dict | None:
    """The managed session that currently OWNS this agent's connection (its manager holds the lease), or None. Used to keep two real sessions from acting on the same
    request: while a managed connection is live, only that session may acknowledge or answer as this agent."""
    import fcntl
    folder = os.path.join(root, "connection", agent)
    lock = os.path.join(folder, "lease.lock")
    try:
        if os.path.islink(folder) or os.path.islink(lock):
            return None
        fd = os.open(lock, os.O_RDWR)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            held = True
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            held = False
    finally:
        os.close(fd)
    if not held:
        return None
    doc = _read_json(os.path.join(folder, "lease.json")) or {}
    return {"session_id": doc.get("session_id"), "provider": doc.get("provider"), "pid": doc.get("pid")}


def _guard_owner(root: str, actor: str, session_ref: str) -> None:
    """Refuse a write as ``actor`` from any session other than the managed one while a managed connection holds the lease."""
    if actor not in RECIPIENTS:
        return
    owner = connection_owner(root, actor)
    if owner is not None and session_ref != owner.get("session_id"):
        raise Refused("owned_by_managed_session", f"{actor} is connected through a managed session ({owner.get('provider')}, session {owner.get('session_id')}). "
                      "Only that session can acknowledge or answer as this agent; a second session would be a second writer.")


def arm(root: str, actor: str) -> None:
    """A watcher says "a session is listening for ``actor``" (model-free). Only a touch; the file holds nothing."""
    if actor not in RECIPIENTS:
        raise Refused("invalid", "Only claude-code or codex can arm a listener.")
    _mkdir(os.path.join(root, "pending"))
    path = os.path.join(root, "pending", f"{actor}.armed")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.utime(fd, None)
    finally:
        os.close(fd)


def unarm(root: str, actor: str) -> None:
    """Remove the "a session is listening" marker (the connection is blocked, offline or on the wrong session)."""
    if actor not in RECIPIENTS:
        return
    path = os.path.join(root, "pending", f"{actor}.armed")
    try:
        if not os.path.islink(path):
            os.unlink(path)
    except FileNotFoundError:
        pass


def armed_age(root: str, actor: str) -> float | None:
    """Seconds since that listener last said it was there, or None when it never has."""
    if actor not in RECIPIENTS:
        return None
    path = os.path.join(root, "pending", f"{actor}.armed")
    try:
        return max(0.0, time.time() - os.stat(path).st_mtime)
    except OSError:
        return None
