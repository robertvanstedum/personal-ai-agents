"""The job routes (overnight build, step 3). All of them are owner-only and answer that jobs are off while
``MINIMOI_GUILD_JOBS`` is off.

  POST /mc/jobs                                   start a job from a kept, on-the-record note
  GET  /conversations/<cid>/jobs                  this conversation's jobs, newest first (the page polls this)
  POST /conversations/<cid>/jobs/<job_id>/stop    Stop (works while off the record, like the turn Stop)
  GET  /conversations/<cid>/jobs/<job_id>/result  the whole result, as a download
  GET  /conversations/<cid>/jobs/<job_id>/note    the result note, rendered, so the page can add it live

The deterministic spoken commands ("run this as a job", "stop") are handled by ``job_command``, which the two turn routes
call before anything is sent to a model."""
from __future__ import annotations

import re

from flask import current_app, jsonify, request

from .security import check_stop, json_error

ANNOUNCE = "This is a job: I'll run it in the background and tell you when it's done; say stop to cancel."
OFF_WORDS = "Jobs are not switched on. Nothing was started."
REFUSAL_STATUS = {"queue_full": 409, "not_connected": 503, "not_a_conversation": 422, "bad_ids": 422, "bad_job_id": 422}


def _api():
    from . import api
    return api


def manager():
    return _api().cfg().get("jobs")


def _view_answer(mgr, job, **extra):
    return {"job": mgr.view(job), **extra}


def start_refusal(code: str, message: str):
    status = REFUSAL_STATUS.get(code)
    if status is None:
        status = 409 if code in ("exists", "quota") else 422 if code in ("no_original", "too_big") else 409
    return json_error(code, message, status)


def job_command(found):
    """For a kept note that is a spoken job command, the server's answer (a JSON response); None when the note is an
    ordinary message and the turn goes on as usual. ``found`` is what ``_mc_prelude`` returned."""
    mgr = manager()
    if mgr is None:
        return None
    from .jobs import ACTIVE, interpret
    services, principal, conv, floor, note, note_id, request_id, attachment_ids = found
    delegate = delegation_command(note.get("text", ""))
    if delegate is not None:
        if attachment_ids:
            return start_refusal("files_not_supported", "Delegated work cannot take attached files yet. Nothing was started.")
        if delegate["agent"] != "claude-code":
            return start_refusal("delegate_unavailable", "Codex can't take delegated work yet: there is no way to cap "
                                 "its cost. Nothing was started. Ask Claude Code instead.")
        job, refusal = mgr.start(conv, principal, note, note_id, None, title=delegate["title"], agent=delegate["agent"])
        if refusal is not None:
            return start_refusal(*refusal)
        mgr.wake.set()
        return jsonify({"job_command": "delegate", "status": "job", "job": mgr.view(job),
                        "message": delegate["announce"]}), 200
    command = interpret(note.get("text", ""))
    if command is None:
        return None
    if command == "stop":
        active = [j for j in mgr.store.list(conv.get("id", "")) if j.get("principal") == principal and j["state"] in ACTIVE]
        if not active:
            return None                                              # nothing to stop: an ordinary message
        job, outcome = mgr.stop(conv["id"], principal, active[0]["id"])
        mgr.wake.set()
        return jsonify({"job_command": "stop", "status": "job", "result": outcome, "job": mgr.view(job) if job else None,
                        "message": mgr.view(job)["message"] if job else "No such job."}), 200
    job, refusal = mgr.start(conv, principal, note, note_id, list(attachment_ids) or None)
    if refusal is not None:
        return start_refusal(*refusal)
    mgr.wake.set()
    return jsonify({"job_command": "run_job", "status": "job", "job": mgr.view(job), "message": ANNOUNCE}), 200


_DELEGATE = re.compile(r"^(?:please\s+)?ask\s+(?P<who>claude\s+code|codex)\s+to\s+(?:research|look\s+into|investigate|find\s+out)\s*:?\s+(?P<what>\S.{4,1900})$",
                       re.I | re.S)
DELEGATION_VAR = "MINIMOI_MC_DELEGATION"


def delegation_command(text):
    """'Ask Claude Code to research: <question>' (also look into / investigate / find out). Read-only research in a
    fresh checkout; the result comes back as this job's result note. Off unless MINIMOI_MC_DELEGATION=1."""
    from . import feature_environ
    if feature_environ().get(DELEGATION_VAR) != "1" or not isinstance(text, str):
        return None
    t = text.strip()
    if any(c in t for c in ('"', "“", "”", "`")) or t.startswith(">"):
        return None
    m = _DELEGATE.match(t)
    if m is None:
        return None
    agent = "claude-code" if m.group("who").lower().startswith("claude") else "codex"
    who = "Claude Code" if agent == "claude-code" else "Codex"
    what = m.group("what").strip()
    return {"agent": agent, "title": f"{who}: {what[:60]}",
            "announce": (f"I've asked {who} to research this in a fresh, read-only copy of the repository. "
                         "It runs in the background on the Mac; I'll post its answer here when it's done. Say stop to cancel.")}


class Decision:
    """What the server decided about a conversation-lane reply: the text to keep as the reply, and the job it started (a view)
    when it started one."""

    def __init__(self, text, job=None):
        self.text, self.job = text, job


class JobHook:
    """Lets the conversation lane PROPOSE work. The model may start a reply with the single line ``JOB: <title>`` (a background job) or
    ``ASK <claude-code|codex|both>: <title>`` (a request to an existing agent session, MC front door); this object, not the model, decides what
    that means. It never lets the model's words become the task (the task is the owner's kept note), acts on at most one proposal per turn, and
    tells the owner honestly when a proposal could not be carried out. Either half can be off: with jobs off a ``JOB:`` line is plain text,
    with agent requests off an ``ASK`` line is plain text."""

    MAX_HOLD = 100                       # a first line longer than this is not a proposal

    def __init__(self, manager, conv, principal, note, note_id, doc_ids, asks=None):
        self.manager, self.conv, self.principal, self.note, self.note_id = manager, conv, principal, note, note_id
        self.doc_ids = list(doc_ids or []) or None
        self.asks = asks
        self._buffer = None
        from flask import copy_current_request_context, has_request_context
        from .meeting_action import enabled, execute
        self._meeting = None
        if has_request_context() and enabled():
            @copy_current_request_context
            def meeting(text):
                return execute(text, self.note, self.note_id, self.conv)
            self._meeting = meeting
        self._evidence = None
        if has_request_context() and asks is not None:
            @copy_current_request_context
            def evidence(mode, rid):
                from .agent_evidence import choose, stage
                from .api import _conversations
                from .board_api import _media, _owner
                chosen = choose(_conversations().get(self.conv.get("id"), self.principal), mode, self.doc_ids)
                return stage(self.asks.root, rid, chosen, store=_conversations(), media=_media(), cid=self.conv.get("id"), principal=self.principal, owner=_owner())
            self._evidence = evidence

    @classmethod
    def holding(cls, text: str) -> bool:
        """True while the text so far could still turn out to be a ``JOB: `` first line: the browser is shown nothing yet."""
        if "\n" in text or len(text) > cls.MAX_HOLD:
            return False
        return "JOB: ".startswith(text[:5]) if len(text) < 5 else text.startswith("JOB: ")

    def holds(self, text: str) -> bool:
        """Like ``holding``, for whichever proposals are switched on (a job line, an ASK line)."""
        if "\n" in text:
            return False
        if self._meeting is not None and len(text) <= 170 and ("MEETING ".startswith(text) or text.startswith("MEETING ")):
            return True
        if self.manager is not None and len(text) <= self.MAX_HOLD and self.holding(text):
            return True
        if self.asks is not None:
            from .agent_ask import TITLE_ACCEPT_MAX, holding_prefix
            return len(text) <= TITLE_ACCEPT_MAX + 40 and holding_prefix(text)         # a long first line is still held, never shown as raw "ASK ..." text
        return False

    def buffers_whole_reply(self) -> bool:
        """True when nothing of this turn's reply may be streamed: agent requests are on and the turn could plausibly produce one (see AskService.may_carry_a_request). The reply is shown
        whole at the finish, or replaced by the platform's outcome: a false "queuing" sentence can never appear first. The cost is that such a reply arrives at once, not word by word."""
        from .meeting_action import authorized_text
        if self._meeting is not None and authorized_text((self.note or {}).get("text", "")):
            return True
        if self.asks is None:
            return False
        if self._buffer is None:                                       # decided once per turn (it scans the conversation's requests)
            try:
                self._buffer = bool(self.asks.may_carry_a_request((self.note or {}).get("text", ""), (self.conv or {}).get("id", "")))
            except Exception:
                self._buffer = True
        return self._buffer

    def visible_prefix(self, text: str) -> str:
        """For a streaming reply: the part that may be shown now (nothing from a possible ASK line on). Without agent requests switched on, all of it."""
        if self.asks is None:
            return text
        from .agent_ask import visible_prefix
        return visible_prefix(text)

    def first_line_candidate(self, text: str) -> bool:
        if "\n" not in text:
            return False
        return (self.manager is not None and text.startswith("JOB: ")) or (self.asks is not None and text.startswith("ASK "))

    def decide(self, text: str, *, clean: bool):
        if text.strip().startswith("MEETING "):
            if self._meeting is None:
                return Decision("Meeting setup is not available right now.")
            if not clean:
                return Decision("No meeting was created: the response was interrupted.")
            try:
                return Decision(self._meeting(text))
            except Exception:
                return Decision("Meeting setup could not be confirmed. Retry the same request; do not assume another room is needed.")
        if self.manager is not None:
            from .jobs import parse_proposal
            proposal = parse_proposal(text, ended_cleanly=clean)
            if proposal is not None and self.asks is not None:
                from .agent_ask import MULTIPLE, scan_asks
                if scan_asks(text, ended_cleanly=clean) is not None:       # a job AND an ask in one reply: two proposals, so neither is acted on, and it says so
                    return Decision(MULTIPLE)
            if proposal is not None:
                job, refusal = self.manager.start(self.conv, self.principal, self.note, self.note_id, self.doc_ids, title=proposal["title"])
                rest = proposal["rest"].strip()
                if refusal is not None:
                    words = f"Not started. {refusal[1]}"
                    return Decision((rest + "\n\n" if rest else "") + f"**{words}**")
                self.manager.wake.set()
                return Decision(rest or ANNOUNCE, self.manager.view(job))
        if self.asks is not None:
            from .agent_ask import scan_asks
            found = scan_asks(text, ended_cleanly=clean)
            if found is None:
                return None
            from .agent_ask import MULTIPLE, job_lines
            if "failure" not in found and job_lines(text):               # an ask AND a job line anywhere in one reply (either order): two proposals, so neither is acted on
                return Decision(MULTIPLE)
            if "failure" in found:                                      # ambiguous, malformed or cut off: said plainly, never shown raw, never claimed queued
                return Decision(found["failure"])
            from .agent_ask import owner_ask_proposal
            proposal, refusal = owner_ask_proposal((self.note or {}).get("text", ""), found["proposal"], self.doc_ids)
            if refusal is not None:
                return Decision(refusal)
            try:
                made = self.asks.ask(self.conv, self.note, self.note_id, proposal["to"], files=proposal.get("files"), evidence=self._evidence)
                return Decision(self.asks.words(made))
            except Exception:                                           # nothing may escape: a raw "ASK ..." line must never be shown or kept
                __import__("logging").getLogger("guild_ui.agent_ask").exception("an ask could not be handled")
                return Decision("**Not sent.** The request could not be handled just now, so I cannot say whether it was recorded. Check the conversation before asking again.")
        return None


def make_hook(services, conv, principal, note, note_id, attachment_ids):
    """A JobHook when jobs or agent requests are on, else None (and then a reply is kept exactly as the model wrote it). Never raises: outside a
    request (the operator probes) there is no manager."""
    try:
        mgr = manager()
    except Exception:
        mgr = None
    try:
        from .agent_ask import service as _asks
        asks = _asks()
    except Exception:
        asks = None
    if mgr is None and asks is None:
        return None
    return JobHook(mgr, conv, principal, note, note_id, attachment_ids, asks)


def _owned(cid, job_id):
    """(manager, job, refusal)."""
    mgr = manager()
    if mgr is None:
        return None, None, json_error("jobs_off", OFF_WORDS, 409)
    api = _api()
    principal = api._principal()
    from .jobs import CONV_RE, JOB_RE
    if not CONV_RE.fullmatch(str(cid)) or not JOB_RE.fullmatch(str(job_id)):
        return None, None, json_error("not_found", "There is no such job. Nothing was changed.", 404)
    job = mgr.store.get(cid, job_id)
    if job is None or job.get("principal") != principal:
        return None, None, json_error("not_found", "There is no such job. Nothing was changed.", 404)
    return mgr, job, None


def _wrap(fn):
    return _api().owner_api(fn)


def job_start():
    api = _api()
    mgr = manager()
    if mgr is None:
        return json_error("jobs_off", OFF_WORDS, 409)
    refusal, found = api._mc_prelude()
    if refusal is not None:
        return refusal
    services, principal, conv, floor, note, note_id, request_id, attachment_ids = found
    from .jobs import job_id_for
    already = mgr.store.get(conv.get("id", ""), job_id_for(note_id)) is not None
    job, refusal = mgr.start(conv, principal, note, note_id, list(attachment_ids) or None)
    if refusal is not None:
        return start_refusal(*refusal)
    mgr.wake.set()
    return jsonify({"result": "already_started" if already else "started", "job": mgr.view(job), "message": ANNOUNCE})


def jobs_list(cid):
    api = _api()
    mgr = manager()
    if mgr is None:
        return jsonify({"enabled": False, "jobs": []})
    conv, refusal = api._conversation(cid)
    if refusal is not None:
        return refusal
    return jsonify({"enabled": True, "jobs": mgr.views(conv["id"], api._principal())})


def job_stop(cid, job_id):
    refusal = check_stop(_api().cfg()["base_url"])
    if refusal is not None:
        return refusal
    mgr, job, refusal = _owned(cid, job_id)
    if refusal is not None:
        return refusal
    job, outcome = mgr.stop(cid, job["principal"], job_id)
    mgr.wake.set()
    view = mgr.view(job)
    return jsonify({"result": outcome, "job": view, "message": view["message"]})


def job_result(cid, job_id):
    from urllib.parse import quote
    mgr, job, refusal = _owned(cid, job_id)
    if refusal is not None:
        return refusal
    text = mgr.store.read_result(cid, job_id)
    if text is None:
        return json_error("not_found", "No result is kept for that job.", 404)
    safe = re.sub(r"[^\w .()+,\-]", "_", job.get("title") or "job")[:60].strip() or "job"
    response = current_app.response_class(text, mimetype="text/plain")
    response.headers["Content-Type"] = "text/plain; charset=utf-8"
    response.headers["Content-Disposition"] = (f"attachment; filename=\"{safe.encode('ascii', 'replace').decode()} - result.txt\"; "
                                               f"filename*=UTF-8''{quote(safe + ' - result.txt')}")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


def job_note(cid, job_id):
    from .markdown_render import with_html
    api = _api()
    mgr, job, refusal = _owned(cid, job_id)
    if refusal is not None:
        return refusal
    if not job.get("result_note_id"):
        return json_error("not_found", "That job has no result note.", 404)
    conv, refusal = api._conversation(cid)
    if refusal is not None:
        return refusal
    try:
        note = api._conv_floor(conv).get_note(job["result_note_id"])
    except Exception:
        return json_error("unavailable", "The result note could not be read right now.", 503)
    if note is None:
        return json_error("not_found", "That job's result note was not found.", 404)
    with_html([note])
    return jsonify({"note": note})


RULES = [
    ("/mc/jobs", "api_job_start", job_start, ["POST"]),
    ("/conversations/<cid>/jobs", "api_jobs_list", jobs_list, ["GET"]),
    ("/conversations/<cid>/jobs/<job_id>/stop", "api_job_stop", job_stop, ["POST"]),
    ("/conversations/<cid>/jobs/<job_id>/result", "api_job_result", job_result, ["GET"]),
    ("/conversations/<cid>/jobs/<job_id>/note", "api_job_note", job_note, ["GET"]),
]
