"""The CoS chat's proposal calls: the same reviewed decision, behind the owner gate and the write guard."""
from pathlib import Path

import requests
from flask import Flask

from minimoi_portal.guild_ui import tasks_owner_api as api

ROOT = Path(__file__).resolve().parents[3]


class Client:
    def __init__(self, rows=None, error=None):
        self.rows, self.error = rows or [], error

    def pending(self):
        if self.error:
            raise self.error
        return self.rows

    def unfinished(self):
        return []


def _status(got):
    return (got[0], got[1]) if isinstance(got, tuple) else (got, got.status_code)


def _read(monkeypatch, client):
    monkeypatch.setattr(api, "_client", lambda user=None: client)
    with Flask(__name__).test_request_context():
        response, status = _status(api.cos_proposals())
        return status, response.get_json()


def test_a_proposal_says_which_chat_it_came_from(monkeypatch):
    row = {"interpretation_id": "IN-1", "sources": [{"source": {"store": "cos-history", "id": "c-1:t-9"}}]}
    assert _read(monkeypatch, Client([row]))[1]["proposals"][0]["conversation_id"] == "c-1"


def test_a_proposal_from_elsewhere_has_no_chat_and_still_shows(monkeypatch):
    row = {"interpretation_id": "IN-1", "sources": [{"source_event_id": "SE-1"}]}
    assert _read(monkeypatch, Client([row]))[1]["proposals"][0]["conversation_id"] is None


def test_a_connection_error_says_unavailable_not_a_500(monkeypatch):
    status, body = _read(monkeypatch, Client(error=requests.ConnectionError("no route")))
    assert status == 503 and body["error"] == "unavailable"


def test_no_task_service_says_so(monkeypatch):
    status, body = _read(monkeypatch, None)
    assert status == 409 and body["error"] == "unavailable"


def test_a_decision_needs_exactly_the_proposal_shown(monkeypatch):
    monkeypatch.setattr(api, "_client", lambda user=None: Client())
    with Flask(__name__).test_request_context():
        for action, body in [("confirm", {}), ("approve", {"interpretation_id": "x"}), ("confirm", "nope")]:
            response, status = _status(api.decide_proposal(action, body))
            assert status == 422 and response.get_json()["error"] == "invalid"


def test_the_routes_sit_behind_the_owner_gate_and_the_write_guard():
    app = (ROOT / "minimoi_portal/app.py").read_text()
    read = app.split('@app.route("/app/cos/task-proposals", methods=["GET"])', 1)[1].split("def ", 1)[0]
    write = app.split('@app.route("/app/cos/task-proposals/<decision>", methods=["POST"])', 1)[1].split("def ", 1)
    assert "@_require_owner" in read and "@_require_owner" in write[0]
    assert '_cos_write_refused("task-proposals")' in write[1].split("@app.route", 1)[0]
