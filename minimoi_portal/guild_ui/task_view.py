"""Owner task view; reads the same scoped service as the existing owner actions.

No new writer or authority path. Mutations remain tasks_owner_api's signed,
digest-bound commands. A recovery link resumes only after a fresh owner click.
"""
from flask import jsonify, render_template, request

from . import owner_page, cfg
from . import tasks_owner_api as actions
from .security import json_error
from services.partnership_tasks.client import TaskServiceUnavailable


@owner_page
def page():
    from .pages import _context
    context = _context("tasks", "Build", "Tracked work", page_open=True)
    user = cfg()["current_user"]() or {}
    context["page_json"]["task_owner"] = str(user.get("auth_id") or user.get("username") or "")
    return render_template("guild_floor/tasks.html", **context)


def overview():
    client = actions._client()
    if client is None:
        return json_error("unavailable", "Task tracking is not connected here yet.", 409)
    try:
        proposals = client.pending()
        open_rows = client.open_work()
        rows = []
        for row in open_rows[:20]:
            record = client.get(row["type"], row["id"])
            if record is None:
                return json_error("unconfirmed", "A listed item could not be read. Refresh before acting.", 503)
            if record.get("state") not in {"task": {"submitted", "working", "waiting"},
                                                "request": {"recorded", "active"}}.get(row["type"], set()):
                continue
            rows.append({**row, "state": record.get("state"), "outcome": record.get("outcome") or (record.get("intent") or {}).get("text", ""),
                         "lead": record.get("lead"), "holder": record.get("holder")})
        return jsonify(proposals=proposals, work=rows, more=len(open_rows) > 20, total=len(open_rows))
    except TaskServiceUnavailable:
        return json_error("unavailable", "The task service could not confirm the current state. Try Refresh.", 503)


def item(task_id):
    if not actions._TID.fullmatch(task_id):
        return json_error("invalid", "Choose a tracked task.", 422)
    client = actions._client()
    if client is None:
        return json_error("unavailable", "Task tracking is not connected here yet.", 409)
    try:
        task = client.get("task", task_id)
        if task is None:
            return json_error("not_found", "That task is not available to you.", 404)
        return jsonify(task=task)
    except TaskServiceUnavailable:
        return json_error("unavailable", "The task could not be read. Refresh before acting.", 503)


def recover():
    iid, digest = request.args.get("interpretation_id", ""), request.args.get("proposal_digest", "")
    if not actions._IID.fullmatch(iid) or not actions._DIGEST.fullmatch(digest):
        return json_error("invalid", "The confirmation link is incomplete.", 422)
    client = actions._client()
    if client is None:
        return json_error("unavailable", "Task tracking is not connected here yet.", 409)
    try:
        proposal, refusal = actions._proposal(client, iid, digest)
        return refusal if refusal is not None else jsonify(proposal=proposal)
    except TaskServiceUnavailable:
        return json_error("unavailable", "Confirmation status is unavailable. Nothing was retried.", 503)


RULES = [("/tasks/view", "api_tasks_view", overview, ["GET"]),
         ("/tasks/item/<task_id>", "api_tasks_item", item, ["GET"]),
         ("/tasks/recover", "api_tasks_recover", recover, ["GET"])]
