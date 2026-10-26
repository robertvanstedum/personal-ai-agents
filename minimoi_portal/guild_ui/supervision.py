"""Build supervision in the portal (#365; core/supervision). Three pieces, all off unless MINIMOI_SUPERVISION=1:

* ``attach``: the Mac sync's one path on the public port, ``/internal/supervision/v1/worker``, authenticated by its
  own token (only its SHA-256 is configured: MINIMOI_SUPERVISION_WORKER_TOKEN_SHA256). It can report stop lines and
  ask decisions; it can never answer one.
* ``owner_decision_command``: Robert's own Guild turn "approve D-12" / "no D-12" answers that decision. Only his kept
  owner note counts (never MC's text); the receipt names the conversation and note and quotes him.
* ``context_block``: what MC is told on each of Robert's Guild turns: decisions waiting on him and the last check-in,
  so MC raises them first.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import time

PATH = "/internal/supervision/v1/worker"
_log = logging.getLogger("supervision")
_COMMAND = re.compile(r"^(?P<word>approve|approved|yes|ok|no|decline|declined|hold)[,:]?\s+(?:decision\s+)?"
                      r"(?P<code>D-?\d{1,9})\s*[.!]?$", re.I)


def enabled(environ) -> bool:
    return environ.get("MINIMOI_SUPERVISION") == "1" and bool(os.environ.get("DATABASE_URL"))


def store():
    import psycopg2
    from core.supervision.store import Store
    dsn = os.environ["DATABASE_URL"]
    return Store(lambda: psycopg2.connect(dsn, connect_timeout=5, options="-c statement_timeout=5000"))


def door(app, path, endpoint, digest, handler, *, refused=(ValueError,), clock=time.monotonic, per_minute=30,
         body_max=65536):
    """One machine path on the public port: a per-address rate limit, a constant-time check of the caller's bearer
    token against its configured SHA-256, a bounded JSON body, and ``handler(body) -> dict``. Malformed or refused
    bodies answer 400; anything else 503, with no detail."""
    from flask import jsonify, request
    seen = {}

    def view():
        now, who = clock(), request.headers.get("X-Real-IP") or request.remote_addr or "-"
        window = [t for t in seen.get(who, ()) if now - t < 60]
        if len(window) >= per_minute:
            return jsonify(error="rate_limited"), 429
        seen[who] = window + [now]
        if len(seen) > 1000:
            seen.clear()
        header = request.headers.get("Authorization", "")
        presented = header[7:] if header.startswith("Bearer ") else ""
        if not (32 <= len(presented) <= 256 and secrets.compare_digest(
                hashlib.sha256(presented.encode()).hexdigest(), digest)):
            return jsonify(error="unauthorized"), 401
        if request.mimetype != "application/json" or (request.content_length or 0) > body_max:
            return jsonify(error="request_invalid"), 400
        try:
            answer = handler(json.loads(request.get_data(cache=False)[:body_max + 1]))
        except refused:
            return jsonify(error="request_invalid"), 400
        except Exception:
            _log.exception("%s call failed", endpoint)
            return jsonify(error="unavailable"), 503
        return jsonify(answer)

    app.add_url_rule(path, endpoint, view, methods=["POST"])


def attach(app, environ, *, store_factory=store, clock=time.monotonic, per_minute=30):
    """Open the Mac sync's path, or do nothing."""
    digest = (environ.get("MINIMOI_SUPERVISION_WORKER_TOKEN_SHA256") or "").strip().lower()
    if not enabled(environ) or not re.fullmatch(r"[0-9a-f]{64}", digest) or "supervision_worker" in app.view_functions:
        return False                                   # off, or already opened by another Guild mount
    from core.supervision.store import Refused
    door(app, PATH, "supervision_worker", digest, lambda body: store_factory().worker(body),
         refused=(ValueError, Refused), clock=clock, per_minute=per_minute)
    _log.info("supervision: Mac sync path open (%s only)", PATH)
    return True


def attach_runner_door(app, environ, *, store_factory=None):
    """The Mac delegation runner's path to production's dispatcher (core/mc_delegation_dispatcher/door.py): on only
    with delegation on and a runner token hash (MINIMOI_MC_DELEGATION_RUNNER_TOKEN_SHA256)."""
    from core.mc_delegation_dispatcher.door import PATH as RUNNER_PATH, BODY_MAX, Refused, handle
    digest = (environ.get("MINIMOI_MC_DELEGATION_RUNNER_TOKEN_SHA256") or "").strip().lower()
    principal, env_name = environ.get("MINIMOI_MC_DELEGATION_PRINCIPAL"), environ.get("MINIMOI_MC_DELEGATION_ENV")
    if (environ.get("MINIMOI_MC_DELEGATION") != "1" or not re.fullmatch(r"[0-9a-f]{64}", digest) or not principal
            or env_name not in ("dev", "prod") or not os.environ.get("DATABASE_URL")
            or "delegation_runner" in app.view_functions):
        return False
    if store_factory is None:
        def store_factory():
            import psycopg2
            from core.mc_delegation_dispatcher.store import DispatcherStore
            dsn = os.environ["DATABASE_URL"]
            return DispatcherStore(lambda: psycopg2.connect(dsn, connect_timeout=5), principal=principal,
                                   environment=env_name)
    # A pass is a heartbeat, a claim and, while a job runs, a renew and a cancel check every few seconds.
    door(app, RUNNER_PATH, "delegation_runner", digest, lambda body: handle(store_factory(), body),
         refused=(ValueError, Refused), per_minute=120, body_max=BODY_MAX)
    _log.info("delegation: runner door open (%s only)", RUNNER_PATH)
    return True


def parse(text):
    """('approved' | 'declined', 'D-12') for Robert's whole short message naming a decision, else None."""
    if not isinstance(text, str) or len(text) > 60:
        return None
    m = _COMMAND.match(text.strip())
    if not m:
        return None
    word = m.group("word").lower()
    answer = "declined" if word in ("no", "decline", "declined", "hold") else "approved"
    return answer, "D-" + m.group("code").upper().lstrip("D-")


def owner_decision_command(found, *, environ, store_factory=store):
    """Robert answers a decision by name in his own Guild turn. Returns a platform result dict, or None for an ordinary
    message (the model answers it)."""
    services, principal, conv, floor, note, note_id, request_id, attachment_ids = found
    if not enabled(environ) or attachment_ids:
        return None
    owner = environ.get("MINIMOI_SUPERVISION_OWNER")
    if not owner or principal != owner or note.get("author_kind", "owner") != "owner" or note.get("who", principal) != principal:
        return None
    parsed = parse(note.get("text"))
    if parsed is None:
        return None
    answer, code = parsed
    s = store_factory()
    decision = s.by_code(code)
    if decision is None:
        return {"reply": f"There is no decision {code}.", "operation": {"type": "owner_decision", "state": "refused"}}
    if decision["state"] not in ("new", "sent"):
        return {"reply": f"{code} is already {decision['state']}; nothing changed.",
                "operation": {"type": "owner_decision", "state": "refused"}}
    receipt = {"channel": "guild", "principal": principal, "conversation_id": conv["id"], "note_id": note_id,
               "text": note.get("text"), "date": int(time.time()), "decision_sha256": decision["text_sha256"]}
    done = s.decide(decision["id"], answer, receipt, text_sha256=decision["text_sha256"])
    if done is None:
        return {"reply": f"{code} could not be recorded (expired or changed); nothing changed.",
                "operation": {"type": "owner_decision", "state": "refused"}}
    return {"reply": f"Recorded: {code} {answer}, in your words. The agent that asked gets it next.\n\n“{decision['text']}”",
            "operation": {"type": "owner_decision", "state": "verified", "decision": code, "answer": answer}}


def context_block(principal, *, environ, store_factory=store):
    """Trusted platform context for MC on Robert's turns: decisions waiting on him, word for word, and the last
    check-in. Empty when off or when nothing waits."""
    if not enabled(environ) or principal != environ.get("MINIMOI_SUPERVISION_OWNER"):
        return ""
    s = store_factory()
    pending, last = s.pending(), s.latest_checkin()
    if not pending and not last:
        return ""
    lines = ["[Build supervision — platform facts, not instructions]"]
    if pending:
        lines.append("Decisions waiting on Robert (raise these first; he answers with “approve D-n” or “no D-n”, or "
                     "by replying on Telegram; you never answer for him):")
        lines.extend(f"- {d['code']}: {d['text']}" for d in pending)
    if last:
        sm = last["summary"]
        lines.append(f"Last check-in {last['at']:%H:%M %Z}: {len(sm.get('prs', []))} open PRs, "
                     f"nudged: {', '.join(sm.get('nudged') or []) or 'none'}.")
        for r in sm.get("running") or []:
            who = "Claude Code" if r["agent"] == "claude-code" else "Codex"
            lines.append(f"- running: {who}, {r['state']} {r['minutes']} min: {r['brief']}")
        if not sm.get("runners"):
            lines.append("- no delegation runner has ever reported: delegated work cannot start")
        for s in sm.get("sessions") or []:
            if s["state"] != "done":
                who = "Claude Code" if s["agent"] == "claude-code" else "Codex"
                lines.append(f"- session {who} {s['session']}: {s['state']} {s['minutes']} min ago"
                             + (f", PR #{s['pr']}" if s.get("pr") else "") + (f": {s['needs']}" if s.get("needs") else ""))
        for i in sm.get("open_items") or []:
            lines.append(f"- {i['kind'].replace('_', ' ')}: {i['text']}")
    return "\n".join(lines)
