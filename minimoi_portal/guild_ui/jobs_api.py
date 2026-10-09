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


class Decision:
    """What the server decided about a conversation-lane reply: the text to keep as the reply, and the job it started (a view)
    when it started one."""

    def __init__(self, text, job=None):
        self.text, self.job = text, job


class JobHook:
    """Lets the conversation lane PROPOSE a job. The model may start a reply with the single line ``JOB: <title>``; this
    object, not the model, decides what that means. It never lets the model's words become the task (the task is the owner's
    kept note), starts at most one job per turn, and tells the owner honestly when a proposed job could not start."""

    MAX_HOLD = 100                       # a first line longer than this is not a proposal

    def __init__(self, manager, conv, principal, note, note_id, doc_ids):
        self.manager, self.conv, self.principal, self.note, self.note_id = manager, conv, principal, note, note_id
        self.doc_ids = list(doc_ids or []) or None

    @classmethod
    def holding(cls, text: str) -> bool:
        """True while the text so far could still turn out to be a ``JOB: `` first line: the browser is shown nothing yet."""
        if "\n" in text or len(text) > cls.MAX_HOLD:
            return False
        return "JOB: ".startswith(text[:5]) if len(text) < 5 else text.startswith("JOB: ")

    @staticmethod
    def first_line_candidate(text: str) -> bool:
        return text.startswith("JOB: ") and "\n" in text

    def decide(self, text: str, *, clean: bool):
        from .jobs import parse_proposal
        proposal = parse_proposal(text, ended_cleanly=clean)
        if proposal is None:
            return None
        job, refusal = self.manager.start(self.conv, self.principal, self.note, self.note_id, self.doc_ids, title=proposal["title"])
        rest = proposal["rest"].strip()
        if refusal is not None:
            words = f"Not started. {refusal[1]}"
            return Decision((rest + "\n\n" if rest else "") + f"**{words}**")
        self.manager.wake.set()
        return Decision(rest or ANNOUNCE, self.manager.view(job))


def make_hook(services, conv, principal, note, note_id, attachment_ids):
    """A JobHook when jobs are on, else None (and then a reply is kept exactly as the model wrote it). Never raises: outside a
    request (the operator probes) there is no manager."""
    try:
        mgr = manager()
    except Exception:
        return None
    return JobHook(mgr, conv, principal, note, note_id, attachment_ids) if mgr is not None else None


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
