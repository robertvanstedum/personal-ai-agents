"""Hand to agents (#298, G1): the owner turns his own approval, in a Rooms meeting, into work requests through the request ledger.

A room turn carries no authority to start work (the meeting on 11 Oct showed both agents saying so). This is the safe path: ONE click by the signed-in owner on HIS OWN
message creates a ledger request per chosen agent. The text of the request comes from the room's record, never from the browser; the agents must be approved; nothing is sent
to an agent that is not; a retry of the same click makes no second request. Model output cannot create one.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import time

from flask import current_app, jsonify

from .security import json_error

AGENTS = ("claude-code", "codex")
NAMES = {"claude-code": "Claude Code", "codex": "Codex"}
DEFAULT_SCOPE = "Spec, build and review as discussed; Dev only; nothing to production; no push or merge."
SCOPE_MAX = 600
INLINE_MAX = 1800          # a message up to this length goes into the request whole; longer ones go as a verified file, with only an excerpt inline
EXCERPT = 1000
FULL_MAX = 20000           # longer than this is refused, never cut
FOLDER = "handoffs"
_ROOM = re.compile(r"[0-9a-f-]{36}")
_TICKETS = re.compile(r"[#0-9 ,]{0,60}")


def _api():
    from . import api
    return api


def handoff():
    from .rooms_archive_api import room_lock,WorkUnknown
    from . import agent_ask
    api=_api();refusal,body=api._write_body()
    if refusal is not None:return refusal
    room,seq,agents=body.get('room'),body.get('seq'),body.get('agents')
    scope=str(body.get('scope') or DEFAULT_SCOPE).strip();tickets=str(body.get('tickets') or '').strip()
    if (not isinstance(room,str) or not _ROOM.fullmatch(room) or type(seq) is not int or seq<1
            or not isinstance(agents,list) or not agents or len(agents)>2
            or any(a not in AGENTS for a in agents) or len(set(agents))!=len(agents)
            or not scope or len(scope)>SCOPE_MAX or not _TICKETS.fullmatch(tickets)):
        return json_error('invalid','Choose one owner message, approved agents and a short scope.',422)
    svc=agent_ask.service();call=current_app.extensions.get('rooms_call')
    if svc is None or call is None:return json_error('unavailable','Rooms handoff admission is unavailable.',409)
    try:
        with room_lock(svc.root,room):
            from .rooms_archive_api import strict_mapping
            if strict_mapping(svc.root,room).get('archive_attempt'):
                return json_error('archive_uncertain','An archive attempt is unresolved; reconcile it before new work.',409)
            status,metadata=call('GET',f'/api/v1/rooms/{room}/archive-check')
            if status!=200:
                return json_error('unavailable','Rooms could not confirm archive state; nothing was sent.',502)
            if not isinstance(metadata,dict) or 'archived_at' not in metadata:
                raise WorkUnknown('archive_state_unknown')
            if metadata['archived_at'] is not None:
                return json_error('archived','Restore this session before handing new work to agents.',409)
            return _handoff_locked()
    except Exception:
        return json_error('unavailable','Rooms handoff admission could not be confirmed. Check before retrying.',409)


def _handoff_locked():
    api = _api()
    from core import agent_requests as ar
    from . import agent_ask, meeting_action, meeting_followup as MF
    svc = agent_ask.service()
    if svc is None:
        return json_error("agent_requests_off", "Handing work to agents is not switched on here. Nothing was sent.", 409)
    refusal, body = api._write_body()
    if refusal is not None:
        return refusal
    room, seq, agents, scope = body.get("room"), body.get("seq"), body.get("agents"), str(body.get("scope") or DEFAULT_SCOPE).strip()
    tickets = str(body.get("tickets") or "").strip()
    if (not isinstance(room, str) or not _ROOM.fullmatch(room) or isinstance(seq, bool) or not isinstance(seq, int) or seq < 1
            or not isinstance(agents, list) or not agents or len(agents) > 2 or any(a not in AGENTS for a in agents) or len(set(agents)) != len(agents)
            or not scope or len(scope) > SCOPE_MAX or not _TICKETS.fullmatch(tickets)):
        return json_error("invalid", "Choose Claude Code and/or Codex, one of your own messages, and a short scope. Nothing was sent.", 422)
    call = current_app.extensions.get("rooms_call")
    who = current_app.extensions.get("rooms_who")
    if call is None or who is None:
        return json_error("rooms_off", "Rooms are not connected here. Nothing was sent.", 409)
    decision, _ = who()
    if not decision.ok or decision.principal != "robert":
        return json_error("not_owner", "Only the owner can hand work to agents. Nothing was sent.", 403)

    def read(path):
        status, data = call("GET", "/api/v1" + path)
        if status != 200 or not isinstance(data, dict):
            raise RuntimeError("unconfirmed")
        return data
    try:
        doc = read(f"/rooms/{room}")
        approvals = read("/approved-agents")
    except RuntimeError:
        return json_error("unavailable", "Rooms could not confirm this just now. Nothing was sent; try again.", 502)
    event = next((e for e in doc.get("events") or [] if isinstance(e, dict) and e.get("seq") == seq), None)
    if event is None or event.get("actor") != "robert" or event.get("kind") != "message":
        return json_error("not_yours", "Only one of your own messages in this room can be handed over. Nothing was sent.", 422)
    approved = {r.get("agent") for r in approvals.get("agents", []) if isinstance(r, dict) and r.get("approved") is True}
    unapproved = [a for a in agents if a not in approved]
    if unapproved:
        return json_error("not_approved", f"{', '.join(NAMES[a] for a in unapproved)} {'is' if len(unapproved) == 1 else 'are'} not approved for Rooms, so nothing was sent.", 409,
                          agents=unapproved)
    conv_id = _source_conversation(room)
    title = str(doc.get("title") or (doc.get("room") or {}).get("title") or "a Rooms meeting")[:120]
    link = f"/guild-next/guild/rooms?room={room}"
    said = svc.scrub(str(event.get("body") or ""))
    if len(said) > FULL_MAX:
        return json_error("too_long", f"That message is {len(said):,} characters, more than the {FULL_MAX:,} that can be handed over whole. Nothing was sent; split it or shorten it.", 422)
    if len(said) <= INLINE_MAX:
        quote = f"His message, verbatim: {said}\n"
    else:                                                       # never cut silently: an excerpt is called an excerpt and the complete message travels as a verified file
        try:
            path, digest = _save_full(svc.root, room, seq, said)
        except OSError:
            return json_error("unavailable", "The full message could not be saved for the agents, so nothing was sent.", 503)
        quote = (f"His message is LONG ({len(said):,} characters). EXCERPT (the first {EXCERPT:,}; NOT the whole message): {said[:EXCERPT]}\n"
                 f"THE COMPLETE MESSAGE is in the file {path} (sha256 {digest[:16]}…). Read all of it BEFORE acting: restrictions and prohibitions may come after the excerpt, and they bind you.\n")
    text = (f"Handed to you by Robert from the Rooms meeting \"{title}\" (room {room}, his message #{seq}). The platform verified it came from his signed-in session.\n"
            + quote +
            f"Scope he set: {svc.scrub(scope)}\n"
            + (f"References: {tickets}\n" if tickets else "")
            + f"Meeting: {link}\n"
            "Do only what his message and the scope authorise. If anything is unclear or outside it, ask him (needs-you) before acting. Nothing here approves production.")
    # Save all deterministic IDs before creating any executable request.
    # A partial/failed admission stays visible as unknown until reconciled.
    try:
        remember(svc.root,room,[{'request_id':ar.request_id_for(f'handoff:{room}:{seq}:{agent}'),
                                'agent':agent,'seq':seq} for agent in agents])
    except Exception:
        return json_error('unavailable','The handoff mapping could not be saved. Nothing was sent.',503)
    made = []
    for agent in agents:
        rid = ar.request_id_for(f"handoff:{room}:{seq}:{agent}")
        try:
            req, created = ar.create_request(svc.root, request_id=rid, frm="robert", to=agent, action=text, conversation_id=conv_id, origin="owner_handoff_from_room")
            made.append({"agent": agent, "name": NAMES[agent], "request_id": req["request_id"], "created": created, "listening": svc.listening(agent), "ok": True})
        except ar.Refused as exc:
            made.append({"agent": agent, "name": NAMES[agent], "ok": False, "error": exc.message})
        except Exception:
            made.append({"agent": agent, "name": NAMES[agent], "ok": None, "error": "The request could not be confirmed. Check the ledger before sending again."})
    return jsonify({"handed": made, "linked_conversation": bool(conv_id), "meeting": link})


MAP_FOLDER = "room-handoffs"
CLIP = 1200


def _map_file(root: str, room: str) -> str:
    return os.path.join(root, MAP_FOLDER, f"{room}.json")


def _mapped(root: str, room: str) -> dict:
    from .rooms_archive_api import strict_mapping
    return strict_mapping(root,room)


def _locked_update(root: str, room: str, change) -> dict:
    from .rooms_archive_api import update_mapping
    return update_mapping(root,room,change)


def remember(root: str, room: str, items: list) -> None:
    """The durable room-to-requests mapping (works for a standalone room too): which handoff requests belong to which room, so their results can be reported there."""
    def add(doc):
        have = {i.get("request_id") for i in doc["items"]}
        doc["items"] += [i for i in items if i["request_id"] not in have]
    _locked_update(root, room, add)


def _clip(text, n=CLIP) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def milestones(doc: dict, agent: str) -> list[tuple[str, str]]:
    """[(key, plain words)] for what has really happened to one handoff request, in order. Receipts and reports are the agent's own records; nothing here claims the work is done
    or verified."""
    from core import agent_requests as ar
    name, out, events = NAMES[agent], [], doc.get("events") or []
    if any(e.get("kind") == "receipt" and e.get("actor") == agent for e in events):
        out.append(("picked_up", f"{name} picked up the handoff."))
    for e in events:
        if e.get("kind") == "needs_you" and e.get("actor") == agent:
            out.append((f"needs_you:{e.get('event_id')}", f"{name} has a question for Robert: {_clip(e.get('text'))}"))
    result = next((e for e in reversed(events) if e.get("kind") == "result" and e.get("actor") == agent), None)
    if result:
        out.append(("reported", f"{name} reported (the agent's own report, not verified): {_clip(result.get('text'))}"))
    failed = None if result else ar.terminal_failure(doc, agent)
    if failed:
        why = ("hit its cost cap for the session" if "max_budget" in failed else "is holding new work because of its usage limits" if ("usage" in failed or "daily_ceiling" in failed)
               else "had its own turn fail")
        out.append((f"failed:{failed[:40]}", f"{name} {why}, so the handoff was not answered ({_clip(failed, 80)}). Nothing was received. It can be handed over again."))
    if doc.get("closed") == "cancelled":
        out.append(("cancelled", f"The handoff to {name} was cancelled."))
    return out


def sync():
    from .rooms_archive_api import room_lock
    from . import agent_ask
    api=_api();refusal,body=api._write_body()
    if refusal is not None:return refusal
    if not isinstance(body.get('room'),str) or not _ROOM.fullmatch(body['room']):
        return json_error('invalid','Name the room.',422)
    svc=agent_ask.service()
    if svc is None:return json_error('unavailable','Handoff state is not connected.',409)
    try:
        with room_lock(svc.root,body.get('room')):
            return _sync_locked()
    except Exception:
        return json_error('unavailable','Handoff state could not be confirmed.',409)


def _sync_locked():
    """Report new milestones of this room's handoffs INTO the room, once each. Safe to call as often as the page likes: every milestone has its own idempotency key and a record
    that it was posted, so reloads, second tabs and retries add nothing."""
    api = _api()
    from core import agent_requests as ar
    from . import agent_ask
    svc = agent_ask.service()
    if svc is None:
        return json_error("agent_requests_off", "Not switched on here.", 409)
    refusal, body = api._write_body()
    if refusal is not None:
        return refusal
    room = body.get("room")
    if not isinstance(room, str) or not _ROOM.fullmatch(room):
        return json_error("invalid", "Name the room.", 422)
    call, who = current_app.extensions.get("rooms_call"), current_app.extensions.get("rooms_who")
    if call is None or who is None:
        return json_error("rooms_off", "Rooms are not connected here.", 409)
    decision, _ = who()
    if not decision.ok or decision.principal != "robert":
        return json_error("not_owner", "Only the owner's page reports handoff progress.", 403)
    posted, items_out, open_count = 0, [], 0
    state = _mapped(svc.root, room)
    done = set(state.get("posted") or [])
    for item in state["items"]:
        rid, agent = item.get("request_id"), item.get("agent")
        if agent not in AGENTS or not ar.valid_request_id(rid):
            continue
        doc = ar.load(svc.root, rid)
        if doc is None:
            continue
        marks = milestones(doc, agent)
        unposted = False
        for key, words in marks:
            mark = f"{rid}:{key}"
            if mark in done:
                continue
            text = f"Platform update from the request ledger, not written by Robert or {NAMES[agent]}: {words}"
            try:
                status, _data = call("POST", f"/api/v1/rooms/{room}/events", {"kind": "message", "body": text}, operation_key=f"handoff:{mark}"[:120])
            except Exception:
                unposted = True
                break                                                        # try again at the next sync; nothing is marked posted
            if status not in (200, 201):
                unposted = True
                break
            done.add(mark)
            posted += 1
        finished = doc.get("closed") is not None or any(k in ("reported",) or k.startswith("failed") for k, _w in marks)
        unposted = unposted or any(f"{rid}:{k}" not in done for k, _w in marks)
        open_count += 0 if (finished and not unposted) else 1            # a milestone that has not reached the room yet keeps this room in the polling loop until it has
        items_out.append({"agent": agent, "request_id": rid, "stage": doc.get("stage"), "closed": doc.get("closed"), "latest": marks[-1][1] if marks else f"Waiting for {NAMES[agent]} to pick it up."})
    if posted:
        _locked_update(svc.root, room, lambda d: d.__setitem__("posted", sorted(set(d.get("posted") or []) | done)))
    return jsonify({"posted": posted, "open": open_count, "items": items_out})


def _save_full(root: str, room: str, seq: int, text: str) -> tuple[str, str]:
    """Write the complete message once, atomically and privately, under the ledger folder; returns (path relative to the ledger folder, sha256). The same message is the same file."""
    folder = os.path.join(root, FOLDER)
    if os.path.islink(folder):
        raise OSError("handoffs folder is a link")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    data = text.encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    name = f"{room}-{seq}-{digest[:12]}.txt"
    final = os.path.join(folder, name)
    if not os.path.isfile(final) or hashlib.sha256(open(final, "rb").read()).hexdigest() != digest:
        tmp = os.path.join(folder, f".{name}.{secrets.token_hex(4)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, final)
    return f"{FOLDER}/{name}", digest


def _source_conversation(room: str):
    """The conversation this meeting was opened from, so receipts and reports come back there (None when the room was not opened from a conversation)."""
    try:
        from . import meeting_followup as MF
        from flask import session
        access_dir = str(current_app.extensions["rooms_access"].directory)
        with MF.database(access_dir) as db:
            row = db.execute("SELECT conversation FROM meeting_rooms WHERE account=? AND room=?", ("account:" + session["user"]["username"], room)).fetchone()
        return row["conversation"] if row else None
    except Exception:
        return None


RULES = [("/rooms/handoff", "api_rooms_handoff", handoff, ["POST"]), ("/rooms/handoff/sync", "api_rooms_handoff_sync", sync, ["POST"])]
