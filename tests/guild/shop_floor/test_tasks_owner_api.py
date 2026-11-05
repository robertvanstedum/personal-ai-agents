"""B4 build step 4: Robert's own task actions in the portal reach the real task service over HTTP, each signed for
the signed-in owner over its exact bytes; confirm promotes exactly as shown; a retry finishes forward; decline and
cancel are recorded owner actions."""
import json
from types import SimpleNamespace

import pytest
from flask import Flask, request

from core import portal_assertion as PA
from core.canonical_json import digest
from minimoi_portal.guild_ui import tasks_owner_api as T
from services.partnership_tasks.client import TaskClient
from services.partnership_tasks.http_api import OWNER_AUDIENCE, OWNER_HEADER, create_app as tasks_app
from tests.partnership.synth import Flow, cmd, service

KEY = ("owner-kid", "partnership-owner-secret-0123456789abcdef")


class Spend:
    def __init__(self):
        self.seen = set()

    def __call__(self, nonce, expires, now=None):
        if nonce in self.seen:
            return False
        self.seen.add(nonce)
        return True


class Session:
    def __init__(self, app):
        self.client = app.test_client()
        self.trust_env = False

    def request(self, method, url, headers=None, data=None, timeout=None, allow_redirects=None):
        r = self.client.open(url.split("http://tasks:8772", 1)[1], method=method, headers=headers, data=data)
        return SimpleNamespace(status_code=r.status_code, json=lambda: json.loads(r.data or b"{}"))


@pytest.fixture
def rig(tmp_path, monkeypatch):
    svc = service(tmp_path / "tasks")
    session = Session(tasks_app(svc, owner_key=KEY, owner_env="test", owner_subject="owner:1", spend=Spend()))

    def sign(method, path, body):
        p, _, q = path.partition("?")
        return {OWNER_HEADER: PA.sign(KEY, audience=OWNER_AUDIENCE, subject="owner:1", method=method,
                                      target=PA.canonical_target(p, q), body=body, env="test")}
    monkeypatch.setattr(T, "_client", lambda user=None: TaskClient("http://tasks:8772", "tok-principal", session=session,
                                                         signer=sign))
    monkeypatch.setattr(T, "_environment", lambda: "synthetic")
    monkeypatch.setattr(T, "_body", lambda: (None, dict(request.get_json(), _key=request.get_json()["idempotency_key"])))
    app = Flask(__name__)
    for rule, endpoint, view, methods in T.RULES:
        app.add_url_rule(rule, endpoint, view, methods=methods)
    f = Flow(svc)
    f.ok(cmd("source_event.record", "source_event", "SE-own-0001",
             {"source": {"store": "cos-history", "id": "cos-owner:t-1", "position": 1},
              "content": "Track a small test task for our codebase map.", "correction_of": None}), "tok-platform")
    f.ok(cmd("interpretation.record", "interpretation", "IN-own-0001",
             {"source_event_ids": ["SE-own-0001"], "correction_event_ids": [], "context_refs": [], "supersedes": None,
              "proposed": {"outcome": "A three-item review checklist.", "scope": "personal", "recipients": ["principal"],
                           "continuing": True,
                           "task": {"lead": "mc", "holder": "mc", "outcome": "A three-item review checklist.",
                                    "done_when": "MC accepts a reviewed checklist.", "return_window": None}}}),
         "tok-cos")
    return svc, app.test_client()


def post(c, path, **body):
    return c.post(path, json={"idempotency_key": "k-" + path.strip("/").replace("/", "-") + "-0001", **body})


def test_robert_sees_the_proposal_and_confirms_it_as_shown(rig):
    svc, c = rig
    pending = c.get("/tasks/pending").json["proposals"]
    assert [p["interpretation_id"] for p in pending] == ["IN-own-0001"]
    shown = pending[0]["proposal_digest"]
    r = post(c, "/tasks/confirm", interpretation_id="IN-own-0001", proposal_digest=shown)
    assert r.status_code == 200 and r.json["state"] == "confirmed", r.json
    task = svc.get("task", r.json["task_id"], "tok-principal")
    assert (task["lead"], task["holder"], task["outcome"]) == ("mc", "mc", "A three-item review checklist.")
    interp = svc.get("interpretation", "IN-own-0001", "tok-principal")
    assert interp["authority"]["kind"] == "owner_confirmation" and interp["provenance"]["origin"] == "owner_session"
    assert c.get("/tasks/pending").json["proposals"] == []


def test_a_changed_or_unknown_proposal_is_not_confirmed(rig):
    svc, c = rig
    r = post(c, "/tasks/confirm", interpretation_id="IN-own-0001", proposal_digest="sha256:" + "0" * 64)
    assert r.status_code == 409
    assert post(c, "/tasks/confirm", interpretation_id="IN-own-9999",
                proposal_digest="sha256:" + "0" * 64).status_code == 409
    assert svc.get("interpretation", "IN-own-0001", "tok-principal")["disposition"] == "proposed"


def test_decline_is_a_recorded_owner_action(rig):
    svc, c = rig
    shown = c.get("/tasks/pending").json["proposals"][0]["proposal_digest"]
    r = post(c, "/tasks/decline", interpretation_id="IN-own-0001", proposal_digest=shown)
    assert r.status_code == 200 and r.json["state"] == "declined"
    assert svc.get("interpretation", "IN-own-0001", "tok-principal")["disposition"] == "rejected"


def test_cancel_records_robert_words_and_withdraws_the_work(rig):
    svc, c = rig
    shown = c.get("/tasks/pending").json["proposals"][0]["proposal_digest"]
    tid = post(c, "/tasks/confirm", interpretation_id="IN-own-0001", proposal_digest=shown).json["task_id"]
    r = post(c, "/tasks/cancel", task_id=tid, text="Cancel this test task; do not save a final recommendation.")
    assert r.status_code == 200 and r.json["state"] == "cancel_recorded", r.json
    assert svc.get("task", tid, "tok-principal")["state"] == "canceled"
    src = svc.get("source_event", "SE-cor-k-tasks-cancel-0001", "tok-principal")
    assert src["content"].startswith("Cancel this test task") and src["correction_of"] == "SE-own-0001"


def test_mc_tool_path_never_imports_the_owner_signer():
    """Design v0.2 §2: MC's model-driven tool listener cannot reach the owner signing helper."""
    import ast
    from pathlib import Path
    for path in (Path("services/mc_tools/listener.py"), *Path("core/mc_tools").glob("*.py")):
        tree = ast.parse(path.read_text())
        names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                 for a in n.names} | {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not any("tasks_owner_api" in (x or "") for x in names), path


def test_a_confirm_that_stopped_part_way_finishes_forward(rig, monkeypatch):
    svc, c = rig
    shown = c.get("/tasks/pending").json["proposals"][0]["proposal_digest"]
    real = T._send
    calls = []

    def flaky(client, cmd):
        calls.append(cmd["kind"])
        if cmd["kind"] == "request.record" and calls.count("request.record") == 1:
            return None, (T.json_error("unconfirmed", "lost", 503))
        return real(client, cmd)
    monkeypatch.setattr(T, "_send", flaky)
    first = post(c, "/tasks/confirm", interpretation_id="IN-own-0001", proposal_digest=shown)
    assert first.status_code == 503
    again = post(c, "/tasks/confirm", interpretation_id="IN-own-0001", proposal_digest=shown)
    assert again.status_code == 200 and again.json["state"] == "confirmed", again.json
    assert svc.get("task", again.json["task_id"], "tok-principal")["lead"] == "mc"


def test_a_promote_whose_reply_was_lost_finishes_forward(rig, monkeypatch):
    svc, c = rig
    shown = c.get("/tasks/pending").json["proposals"][0]["proposal_digest"]
    real = T._send
    lost = []

    def lose_promote_reply(client, cmd):
        out = real(client, cmd)
        if cmd["kind"] == "request.promote" and not lost:
            lost.append(1)
            return None, T.json_error("unconfirmed", "reply lost after commit", 503)
        return out
    monkeypatch.setattr(T, "_send", lose_promote_reply)
    assert post(c, "/tasks/confirm", interpretation_id="IN-own-0001", proposal_digest=shown).status_code == 503
    again = post(c, "/tasks/confirm", interpretation_id="IN-own-0001", proposal_digest=shown)
    assert again.status_code == 200 and again.json["state"] == "confirmed", again.json


# ---- B4 execution design v0.6: Robert approves or declines each work card from his own session -------------------

def _card(card_id):
    return {"schema": "minimoi.card.v1", "card_id": card_id, "class": "build_only", "class_version": 1,
            "repository": "personal-ai-agents", "ref": "a" * 40,
            "workers": {"builder": {"actor": "fakework", "route": "fakework", "amount": "0.25", "cost_cap_usd": "1"}},
            "briefs": {"build": "fakework/1\nresult ok"}, "attempts": 1, "step_deadline_s": 3600}


def _queued_card(svc, c, op, card_id):
    from core.mc_delegation_dispatcher import cards as card_mod
    shown = c.get("/tasks/pending").json["proposals"]
    if shown:
        post(c, "/tasks/confirm", interpretation_id="IN-own-0001", proposal_digest=shown[0]["proposal_digest"])
    tid = "TK-own-0001"
    doc = _card(card_id); d = "sha256:" + card_mod.Catalogue.load().compile(doc).digest
    t = svc.get("task", tid, "tok-principal")
    res = svc.submit(cmd("task.request_admission", "task", tid,
                         {"operation_id": op, "bundle_digest": d,
                          "bundle_ref": {"store": "harness", "id": card_id, "edition": None, "digest": d},
                          "fence_generation": t["fence_generation"], "card": doc}, expected=t["revision"]), "tok-mc")
    assert res["outcome"] == "accepted", res
    return d


def test_v06_robert_sees_the_card_and_approves_exactly_what_he_saw(rig):
    svc, c = rig
    d = _queued_card(svc, c, "OB-own-card-1", "C-own-0001")
    rows = c.get("/tasks/admissions").json["admissions"]
    assert [r["operation_id"] for r in rows] == ["OB-own-card-1"] and rows[0]["card_digest"] == d
    assert rows[0]["card"]["workers"]["builder"]["actor"] == "fakework" and rows[0]["card"]["briefs"]["build"]
    assert post(c, "/tasks/admit", operation_id="OB-own-card-1", card_digest="sha256:" + "0" * 64).status_code == 409
    r = post(c, "/tasks/admit", operation_id="OB-own-card-1", card_digest=d)
    assert r.status_code == 200 and r.json["state"] == "approved", r.json
    ob = svc.get("outbox", "OB-own-card-1", "tok-principal")
    assert ob["approval"]["card_digest"] == d and ob["approval"]["spent_at"] is None
    ev = svc.get("source_event", ob["approval"]["event_id"], "tok-principal")
    assert ev["source"]["store"] == "portal-owner-action" and ev["provenance"]["origin"] == "owner_session"
    again = post(c, "/tasks/admit", operation_id="OB-own-card-1", card_digest=d)
    assert again.status_code == 409                                    # a fresh approval stands; never a second one


def test_v06_robert_declines_a_card_and_nothing_will_be_sent(rig):
    svc, c = rig
    d = _queued_card(svc, c, "OB-own-card-2", "C-own-0002")
    r = post(c, "/tasks/admission-decline", operation_id="OB-own-card-2", card_digest=d)
    assert r.status_code == 200 and r.json["state"] == "declined", r.json
    assert svc.get("outbox", "OB-own-card-2", "tok-principal")["state"] == "declined"
    assert c.get("/tasks/admissions").json["admissions"] == []
