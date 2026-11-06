"""Robert's own task actions in the portal (B4 connection design v0.2 §2, §4.4, §5; v0.3 R-M1, R-S1, R-N2).

Every route here is owner-only (``owner_api``) and is a direct click by the signed-in owner. Each call to the task
service is made with the ``principal`` seat AND a per-request portal assertion over its exact bytes (audience
``partnership-owner``), so the principal token alone never acts as Robert, and MC's tool path, which never imports
this module, cannot produce one.

- ``GET  /tasks/pending``: proposals waiting for him (the task view lists them).
- ``POST /tasks/confirm``: {interpretation_id, proposal_digest, idempotency_key}. The digest must be the one the page
  showed and must still be current. The portal then records the owner-action source (the exact proposal digest) and
  sends dispose (adopted, authority = that source), request.record (responder cos) and request.promote (= the
  confirmed terms). Ids are ``<step>-<interpretation key>``, so a retry finishes forward.
- ``POST /tasks/decline``: {interpretation_id, proposal_digest, idempotency_key}: the source, then dispose rejected.
- ``POST /tasks/correct``: {task_id, text, idempotency_key}: Robert's words as a correction of the task's source;
  then CoS proposes the new reading, and he confirms it in the same view.
- ``POST /tasks/cancel``: {task_id, text, idempotency_key}: Robert's words as a correction, then correction.apply
  with change cancel. Open effects come back for the view to show.

The page itself (the task view) is Codex's work (Robert's UI rule). This module is its backend and contract.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

import requests
from flask import request

from .security import json_error

STORE = "portal-owner-action"
_IID = re.compile(r"IN-[A-Za-z0-9_-]{4,64}")
_TID = re.compile(r"TK-[A-Za-z0-9_-]{4,64}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
SCOPE = "personal"


def _client(user=None):
    """The principal client, signing each request for the signed-in owner. None when not configured. ``user`` is the
    signed-in owner when the caller is outside the Guild mount (the CoS Actions routes in app.py, after their own
    owner gate); otherwise the mount's current user."""
    from core import portal_assertion as PA
    from services.partnership_tasks.client import TaskClient, read_token
    from services.partnership_tasks.http_api import OWNER_AUDIENCE, OWNER_HEADER
    from . import cfg
    url = os.environ.get("PARTNERSHIP_TASKS_URL", "").strip()
    token_file = os.environ.get("PORTAL_PARTNERSHIP_PRINCIPAL_TOKEN_FILE", "").strip()
    key = PA.load_key(os.environ.get("PORTAL_PARTNERSHIP_OWNER_KEY_FILE"))
    env = os.environ.get("PORTAL_PARTNERSHIP_OWNER_ENV", "").strip()
    user = (user if user is not None else cfg()["current_user"]()) or {}
    owner = user.get("auth_id") or user.get("username")
    if not (url and token_file and key and env and owner):
        return None

    def sign(method, path, body):
        p, _, q = path.partition("?")
        return {OWNER_HEADER: PA.sign(key, audience=OWNER_AUDIENCE, subject=f"owner:{owner}", method=method,
                                      target=PA.canonical_target(p, q), body=body, env=env)}
    return TaskClient(url, lambda: read_token(token_file), signer=sign)


def _environment():
    return os.environ.get("PARTNERSHIP_ENVIRONMENT", "dev")


def _body():
    from . import api
    refusal, body = api._write_body()          # size, CSRF, record mode, JSON object, idempotency key
    return refusal, body


def _cmd(kind, ttype, tid, payload, cid, *, expected=None, parent=None):
    from services.partnership_tasks.client import command
    return command(kind, ttype, tid, payload, command_id=cid, environment=_environment(), scope=SCOPE,
                   expected_revision=expected, causal_parent=parent, session_id=None,
                   runtime={"kind": "human", "model": None, "provider": None, "build": "portal/tasks_owner/1",
                            "config_version": None})


def _ref(sid):
    return {"store": "task_service", "id": sid, "edition": None, "digest": None}


def _send(client, cmd):
    """Submit; an accepted or replayed result is success; anything else stops the sequence with its reason."""
    from services.partnership_tasks.client import TaskServiceUnavailable
    try:
        out = client.submit(cmd)
    except TaskServiceUnavailable as exc:
        return None, json_error("unconfirmed", "The task service did not confirm this step; reopen the view to "
                                "finish it.", 503) if exc.answer != "absent" else \
            json_error("not_sent", "That step did not reach the task service; try again.", 503)
    if out.get("outcome") != "accepted":
        code, detail = out.get("code") or "refused", out.get("detail")
        return None, json_error(code, refusal_words(cmd.get("kind"), code, detail), 409, detail=detail)
    return out, None


# What Robert reads when the task service refuses a step: what did not happen, why in plain words, and what to do.
# The raw code and detail still travel in the answer for agents and logs (Blue Lantern, 5 Nov: the page showed only
# "operation", the detail of a binding that did not yet allow outbox.approve).
_STEP = {"outbox.approve": "Not approved", "outbox.record": "Not declined", "request.promote": "Not confirmed",
         "interpretation.dispose": "Not recorded", "correction.apply": "Not changed"}
_WHY = {("not_authorized", "operation"): "this portal is not yet allowed to make that change in the task service",
        ("not_authorized", "approval_digest_mismatch"): "the card changed since the page showed it",
        ("not_authorized", "approval_not_for_this_card"): "your click did not match this card",
        ("not_authorized", "approval_already_used"): "that click was already used for another card",
        ("id_taken", "already_approved"): "it is already approved and waiting to start",
        ("fenced", "task_changed_since_enqueue"): "the task changed since the card was made",
        ("revision_conflict", None): "the record changed while you were looking at it"}


def refusal_words(kind, code, detail):
    step = _STEP.get(kind, "Not done")
    why = _WHY.get((code, detail)) or _WHY.get((code, None))
    if why is None:
        why = f"the task service refused it ({code}{': ' + detail if detail else ''})"
    after = ("Nothing has started. Your click is kept: once this is put right, one more click finishes it."
             if kind == "outbox.approve" and (code, detail) == ("not_authorized", "operation")
             else "Nothing has started. Refresh to see the current card.")
    return f"{step}: {why}. {after}"


def _proposal(client, iid, shown_digest):
    current = next((p for p in client.pending() if p["interpretation_id"] == iid), None)
    if current is None:
        current = _already_confirmed(client, iid)          # a confirm that stopped part-way finishes forward (S6)
    if current is None:
        return None, json_error("not_pending", "That proposal is no longer waiting for you.", 409)
    if current["proposal_digest"] != shown_digest:
        return None, json_error("changed", "The proposal changed since the page showed it; reload and look again.",
                                409)
    return current, None


def _already_confirmed(client, iid):
    """The proposal as it was confirmed by THIS portal's own owner action (its authority ref is our action source),
    so the remaining steps can be sent again under the same ids. None for anything else."""
    from core.canonical_json import digest
    interp = client.get("interpretation", iid)
    want = f"SE-own-{iid[3:]}-act"[:67]
    if not interp or interp.get("disposition") != "adopted" or (interp.get("authority") or {}).get("ref", {}) \
            .get("id") != want:
        return None
    sources = [client.get("source_event", sid) for sid in interp["source_event_ids"]]
    # The dispose is re-sent exactly as first sent: against the revision it adopted (the adoption added one).
    return {"interpretation_id": iid, "revision": interp["revision"] - 1, "proposed": interp["proposed"],
            "proposal_digest": digest(interp["proposed"]),
            "sources": [{"source_event_id": x["source_event_id"]} for x in sources if x], "finishing": True}


def pending():
    client = _client()
    if client is None:
        return json_error("unavailable", "Task tracking is not connected here.", 409)
    from flask import jsonify
    return jsonify(proposals=client.pending())


def _owner_action(client, key, action, content, step="act"):
    sid = f"SE-own-{key}-{step}"[:67]
    return sid, _cmd("source_event.record", "source_event", sid,
                     {"source": {"store": STORE, "id": f"{action}:{key}", "position": 1},
                      "content": json.dumps(content, sort_keys=True), "correction_of": None},
                     f"own-{action}-{key}")


def confirm():
    return _decide("confirm")


def decline():
    return _decide("decline")


def _decide(action):
    refusal, body = _body()
    if refusal is not None:
        return refusal
    iid, shown = body.get("interpretation_id"), body.get("proposal_digest")
    if not isinstance(iid, str) or not _IID.fullmatch(iid) or not isinstance(shown, str) or not _DIGEST.fullmatch(shown):
        return json_error("invalid", "Choose one proposal as the page showed it.", 422)
    client = _client()
    if client is None:
        return json_error("unavailable", "Task tracking is not connected here.", 409)
    current, refusal = _proposal(client, iid, shown)
    if refusal is not None:
        return refusal
    key = iid[3:]
    sid, src = _owner_action(client, key, action, {"action": action, "interpretation_id": iid,
                                                   "proposal_digest": shown})
    if action == "decline":
        # The confirmation check reads only "confirm" actions; a decline is its own recorded owner action.
        _, r = _send(client, src)
        if r is not None:
            return r
        _, r = _send(client, _cmd("interpretation.dispose", "interpretation", iid,
                                  {"disposition": "rejected", "authority": {"kind": "none", "ref": None}},
                                  f"dispose-{key}", expected=current["revision"], parent=_ref(sid)))
        from flask import jsonify
        return r if r is not None else jsonify(state="declined", interpretation_id=iid)
    steps = []
    _, r = _send(client, src)
    if r is not None:
        return r
    _, r = _send(client, _cmd("interpretation.dispose", "interpretation", iid,
                              {"disposition": "adopted", "authority": {"kind": "owner_confirmation", "ref": _ref(sid)}},
                              f"dispose-{key}", expected=current["revision"], parent=_ref(sid)))
    if r is not None:
        return r
    source_id = current["sources"][0]["source_event_id"]
    rid, tid = f"RQ-{key}"[:67], f"TK-{key}"[:67]
    _, r = _send(client, _cmd("request.record", "request", rid,
                              {"source_event_id": source_id, "intent_text": current["proposed"]["outcome"][:500],
                               "interpretation_ref": iid, "responder": "cos"},
                              f"request-{key}", parent={"store": "task_service", "id": iid, "edition": None,
                                                        "digest": None}))
    if r is not None:
        return r
    req = client.get("request", rid)
    terms = current["proposed"]["task"]
    _, r = _send(client, _cmd("request.promote", "request", rid, {"task_id": tid, "parent_ref": None, **terms},
                              f"promote-{key}",
                              # finishing forward: the promote is re-sent against the revision it first promoted
                              expected=(req["revision"] - 1 if req.get("state") == "promoted" else req["revision"])
                              if req else None,
                              parent={"store": "task_service", "id": rid, "edition": None, "digest": None}))
    if r is not None:
        return r
    from flask import jsonify
    return jsonify(state="confirmed", interpretation_id=iid, request_id=rid, task_id=tid, lead=terms["lead"])


def _task_words():
    refusal, body = _body()
    if refusal is not None:
        return refusal, None, None, None
    tid, text = body.get("task_id"), body.get("text")
    if not isinstance(tid, str) or not _TID.fullmatch(tid) or not isinstance(text, str) or not text.strip() \
            or len(text) > 4000:
        return json_error("invalid", "Name the task and say what you want in your own words.", 422), None, None, None
    client = _client()
    if client is None:
        return json_error("unavailable", "Task tracking is not connected here.", 409), None, None, None
    task = client.get("task", tid)
    if task is None:
        return json_error("not_found", "That task is not yours to change.", 404), None, None, None
    return None, client, task, (text.strip(), body["_key"])


def _correction_source(client, task, text, key):
    """Robert's own words, recorded as a correction of the task's source: the portal picks what is corrected from
    the task it is showing, never a model (v0.2 §5)."""
    interp = client.get("interpretation", task["interpretation_ref"])
    corrected = interp["source_event_ids"][0]
    sid = f"SE-cor-{key}"[:67]
    return sid, _cmd("source_event.record", "source_event", sid,
                     {"source": {"store": STORE, "id": f"correct:{key}", "position": 1}, "content": text,
                      "correction_of": corrected}, f"correction-{key}")


def correct():
    refusal, client, task, words = _task_words()
    if refusal is not None:
        return refusal
    text, key = words
    sid, src = _correction_source(client, task, text, key)
    _, r = _send(client, src)
    from flask import jsonify
    return r if r is not None else jsonify(state="correction_recorded", task_id=task["task_id"], source_event_id=sid,
                                           next="CoS proposes the corrected reading; confirm it here.")


def cancel():
    refusal, client, task, words = _task_words()
    if refusal is not None:
        return refusal
    text, key = words
    sid, src = _correction_source(client, task, text, key)
    _, r = _send(client, src)
    if r is not None:
        return r
    out, r = _send(client, _cmd("correction.apply", "request", task["request_ref"],
                                {"correction_event_id": sid, "changes": ["cancel"], "intent_text": None,
                                 "interpretation_ref": None},
                                f"cancel-{key}", expected=(client.get("request", task["request_ref"]) or {}).get("revision"),
                                parent=_ref(sid)))
    if r is not None:
        return r
    from flask import jsonify
    return jsonify(state="cancel_recorded", task_id=task["task_id"], open_effects=out.get("open_effects", []))


# ---- B4 execution design v0.6: Robert approves or declines each work card (the exact approval paid work needs) ----
_OP = re.compile(r"OB-[A-Za-z0-9_-]{4,64}")


def _card_summary(card):
    """What Robert sees before approving: who does it, where, for how much, and the words of every step."""
    if not isinstance(card, dict):
        return None
    workers = {role: {k: w.get(k) for k in ("actor", "route", "amount", "cost_cap_usd")}
               for role, w in (card.get("workers") or {}).items() if isinstance(w, dict)}
    return {"card_id": card.get("card_id"), "class": card.get("class"), "repository": card.get("repository"),
            "ref": card.get("ref"), "workers": workers,
            "briefs": card.get("briefs"), "step_deadline_s": card.get("step_deadline_s"),
            "attempts": card.get("attempts"), "input_artifacts": card.get("input_artifacts") or []}


def _waiting_cards(client):
    """Pending admissions whose task has not moved on: what the work-card list and the Needs-you count both read."""
    return [r for r in client.outbox_pending()
            if r["operation"] == "admit_bundle" and r["state"] == "pending" and not r["stale"]]


def _standing_approval(row, now):
    """The row's approval while it still stands; None once it was spent or ran out, because Robert can then approve
    the card again (the same test the service applies to a new approval)."""
    from services.partnership_tasks.service import APPROVAL_WINDOW_S
    approval = row.get("approval")
    if not approval or approval.get("spent_at") is not None:
        return None
    approved = datetime.fromisoformat(approval["approved_at"].replace("Z", "+00:00"))
    return None if (now - approved).total_seconds() > APPROVAL_WINDOW_S else approval


def _origin_conversation(client, task_id):
    """The CoS conversation a task's proposal came from, or None when it did not come from one or cannot be read now.
    The trail: task -> interpretation -> source events; a source in ``cos-history`` has the id
    ``<conversation_id>:<turn_id>`` (domains/cos/task_tools.py). None only widens where a card shows; it never hides one."""
    from services.partnership_tasks.client import TaskServiceUnavailable
    try:
        ref = (client.get("task", task_id) or {}).get("interpretation_ref")
        interp = client.get("interpretation", ref) if ref else None
        for sid in (interp or {}).get("source_event_ids") or []:
            source = (client.get("source_event", sid) or {}).get("source") or {}
            if source.get("store") == "cos-history":
                conversation, sep, _turn = str(source.get("id") or "").rpartition(":")
                return conversation if sep and conversation else None
    except (TaskServiceUnavailable, requests.RequestException, ValueError):
        return None
    return None


def approvals_waiting(user=None):
    """Task proposals and work cards waiting for Robert, for the Needs-you count, as a SourceResult of rows
    {kind, id, text, at}. None when this host has no task service at all. A service that is configured but cannot be
    read, in any way, is unknown and says only the kind of failure. Short timeouts: every Guild page waits on this."""
    from .adapters.contract import NOT_CONFIGURED, READ_FAILED, live_ok, live_unknown
    evidence = "task service: /v1/pending, /v1/outbox/pending"
    if not os.environ.get("PARTNERSHIP_TASKS_URL", "").strip():
        return None
    try:
        client = _client(user)
        if client is None:
            return live_unknown(NOT_CONFIGURED, "task service connection is incomplete", evidence)
        client.timeout = (1, 3)
        now = datetime.now(timezone.utc)
        rows = [{"kind": "proposal", "id": p["interpretation_id"], "at": p.get("created_at"),
                 "text": f"Proposal waiting for you: {_one_line(p['proposed'].get('outcome'))}"}
                for p in client.pending()]
        for r in _waiting_cards(client):
            if _standing_approval(r, now) is None:
                task = client.get("task", r["task_id"]) or {}
                rows.append({"kind": "card", "id": r["operation_id"], "at": r.get("created_at"),
                             "text": f"Work card waiting for you: {_one_line(task.get('outcome') or r['task_id'])}"})
    except Exception as exc:          # never "nothing needs you" because a read failed; the type names no secret
        return live_unknown(READ_FAILED, f"task service read failed: {type(exc).__name__}", evidence)
    return live_ok(rows, evidence)


def _one_line(text, limit=120):
    words = " ".join(str(text or "no description").split())
    return words if len(words) <= limit else words[:limit - 1].rstrip() + "…"


def admissions(user=None):
    """Work cards waiting for Robert's approval: pending admissions whose task has not moved on."""
    import requests
    from flask import jsonify
    from services.partnership_tasks.client import TaskServiceUnavailable
    client = _client(user)
    if client is None:
        return json_error("unavailable", "Task tracking is not connected here.", 409)
    try:
        now = datetime.now(timezone.utc)
        out = []
        for r in _waiting_cards(client):
            ob = client.get("outbox", r["operation_id"])
            out.append({"operation_id": r["operation_id"], "task_id": r["task_id"], "card_digest": r["bundle_digest"],
                        "approval": _standing_approval(r, now),
                        "conversation_id": _origin_conversation(client, r["task_id"]),
                        "card": _card_summary((ob or {}).get("packet"))})
    except (TaskServiceUnavailable, requests.RequestException):      # a raw connection error must not become a 500
        return json_error("unavailable", "The task service could not confirm the current state. Try Refresh.", 503)
    return jsonify(admissions=out)


def _admission(client, op, shown):
    ob = client.get("outbox", op)
    if ob is None or ob["operation"] != "admit_bundle":
        return None, json_error("not_found", "That work card is not available to you.", 404)
    if ob["state"] != "pending":
        return None, json_error("not_pending", "That work card is no longer waiting for you.", 409)
    if ob["bundle_digest"] != shown:
        return None, json_error("changed", "The work card changed since the page showed it; reload and look again.", 409)
    if not isinstance(ob.get("packet"), dict):
        return None, json_error("no_card", "This admission carries no card to approve.", 409)
    return ob, None


def admit():
    return _admission_decision("admit")


def admission_decline():
    return _admission_decision("decline_admission")


def _admission_decision(action):
    refusal, body = _body()
    if refusal is not None:
        return refusal
    return decide_admission(action, body)


def decide_admission(action, body, user=None):
    """One owner click on one card as the page showed it. Approve: an owner-action event naming exactly this
    operation and card digest, then outbox.approve citing it (the authority re-checks both, within 15 minutes, once).
    Decline: an owner-action event, then outbox.record "declined" (nothing is ever sent). The caller has already
    checked the owner and the write guard (the Guild mount's _body, or the CoS Actions route's CSRF check)."""
    from flask import jsonify
    if action not in ("admit", "decline_admission") or not isinstance(body, dict):
        return json_error("invalid", "Choose one work card as the page showed it.", 422)
    op, shown = body.get("operation_id"), body.get("card_digest")
    if not isinstance(op, str) or not _OP.fullmatch(op) or not isinstance(shown, str) or not _DIGEST.fullmatch(shown):
        return json_error("invalid", "Choose one work card as the page showed it.", 422)
    client = _client(user)
    if client is None:
        return json_error("unavailable", "Task tracking is not connected here.", 409)
    try:
        ob, refusal = _admission(client, op, shown)
    except Exception:
        return json_error("unavailable", "The work card could not be read. Refresh before acting.", 503)
    if refusal is not None:
        return refusal
    # One event per click on this revision: a re-approval after expiry or a resend is a new, separate approval.
    key = f"{op[3:]}-r{ob['revision']}"
    content = ({"action": "admit", "operation_id": op, "card_digest": shown} if action == "admit"
               else {"action": "decline_admission", "operation_id": op, "card_digest": shown})
    sid, src = _owner_action(client, key, "adm" if action == "admit" else "admdecl", content)
    _, r = _send(client, src)
    if r is not None:
        return r
    if action == "admit":
        cmd = _cmd("outbox.approve", "outbox", op, {"card_digest": shown, "approval_event_id": sid},
                   f"approve-{key}", expected=ob["revision"], parent=_ref(sid))
    else:
        cmd = _cmd("outbox.record", "outbox", op, {"observation": "declined", "destination_receipt": None,
                                                   "code": "owner_declined"},
                   f"admdecl-{key}", expected=ob["revision"], parent=_ref(sid))
    _, r = _send(client, cmd)
    if r is not None:
        return r
    return jsonify(state="approved" if action == "admit" else "declined", operation_id=op,
                   note="Approved for 15 minutes; the work starts when the authority admits it." if action == "admit"
                   else "Declined; nothing was sent.")


RULES = [
    ("/tasks/pending", "api_tasks_pending", pending, ["GET"]),
    ("/tasks/confirm", "api_tasks_confirm", confirm, ["POST"]),
    ("/tasks/decline", "api_tasks_decline", decline, ["POST"]),
    ("/tasks/correct", "api_tasks_correct", correct, ["POST"]),
    ("/tasks/cancel", "api_tasks_cancel", cancel, ["POST"]),
    ("/tasks/admissions", "api_tasks_admissions", admissions, ["GET"]),
    ("/tasks/admit", "api_tasks_admit", admit, ["POST"]),
    ("/tasks/admission-decline", "api_tasks_admission_decline", admission_decline, ["POST"]),
]
