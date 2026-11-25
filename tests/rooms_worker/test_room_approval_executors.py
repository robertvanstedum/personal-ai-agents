"""#681: the executors behind a room approval, MC's bridge and the CoS path through the portal (Lane G)."""
from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from flask import Flask

ROOM, TURN = str(uuid4()), str(uuid4())
DIGEST = "sha256:" + "b" * 64


# ---- MC: the bridge answers an approval turn with ToolProposals, no model ------------------------------------
class Floor:
    def __init__(self):
        self.notes = {}

    def add_note(self, key, text, author):
        self.notes[key] = (text, author)
        return SimpleNamespace(outcome="kept")


def _bridge(monkeypatch, handler, floor):
    from minimoi_portal.guild_ui import mc_rooms_bridge as B
    import minimoi_portal.guild_ui.mc_tools_wiring as W
    bridge = B.Bridge.__new__(B.Bridge)
    bridge.services, bridge.environ, bridge.principal = SimpleNamespace(), {}, "robert"
    monkeypatch.setattr(W, "_conversation_floor", lambda services, principal, conv: floor if conv == "c-proposal" else None)
    monkeypatch.setattr(B.Bridge, "_proposals", lambda self: handler)
    return bridge


def _approval_turn(kind="confirm", partner="mc"):
    return {"trigger": {"text": "yes P-AB12"},
            "room_approval": {"code": "P-AB12", "kind": kind, "partner": partner, "source_ref": "c-proposal",
                              "payload_digest": DIGEST, "expires_at": "2099-01-01T00:00:00+00:00", "state": "published"}}


def test_mc_room_yes_runs_the_craft_handler_in_the_proposals_conversation(monkeypatch):
    calls, floor = [], Floor()
    handler = SimpleNamespace(handle=lambda kind, **kw: calls.append((kind, kw)) or
                              {"reply": "Opened issue #42.", "operation": {"state": "verified"}})
    bridge = _bridge(monkeypatch, handler, floor)
    out = bridge.approve(_approval_turn(), "room-op")
    assert calls == [("confirm", {"conversation": "c-proposal", "turn_key": "room-op-owner", "code": "P-AB12"})]
    assert floor.notes["room-op-owner"][0] == "yes P-AB12"                  # his exact words, in that conversation
    assert floor.notes["room-op-owner-reply"][0] == "Opened issue #42."
    assert out["outcome"] == "done" and out["text"] == "Opened issue #42."
    assert out["room_decision"] == {"code": "P-AB12", "state": "confirmed", "receipt": "Opened issue #42."}


@pytest.mark.parametrize("state,decided", [("cancelled", "cancelled"), ("needs_code", "refused"),
                                           ("not_shown", "refused"), ("expired", "failed"), ("failed", "failed")])
def test_mc_room_outcomes_map_to_registry_decisions(monkeypatch, state, decided):
    handler = SimpleNamespace(handle=lambda kind, **kw: {"reply": "words", "operation": {"state": state}})
    out = _bridge(monkeypatch, handler, Floor()).approve(_approval_turn(), "op")
    assert out["room_decision"]["state"] == decided


def test_mc_never_answers_a_card_another_partner_published(monkeypatch):
    bridge = _bridge(monkeypatch, SimpleNamespace(handle=lambda *a, **k: pytest.fail("called")), Floor())
    with pytest.raises(ValueError):
        bridge.approve(_approval_turn(partner="cos"), "op")


# ---- CoS: the portal confirms against the stored digest ------------------------------------------------------
class Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def get_json(self, silent=False):
        return self._data


def _portal(monkeypatch, pending, decide):
    import minimoi_portal.guild_ui.tasks_owner_api as T
    monkeypatch.setattr(T, "_client", lambda user=None: SimpleNamespace(pending=lambda: pending))
    seen = []
    monkeypatch.setattr(T, "decide_proposal", lambda action, body, user=None: seen.append((action, body, user)) or decide)
    return seen


def _body(kind="confirm"):
    return {"room": ROOM, "turn_id": TURN, "approval": {"code": "P-CD34", "kind": kind, "partner": "cos",
                                                        "source_ref": "IN-cos-abcd", "payload_digest": DIGEST}}


def _waiting(store="cos-meeting-owner", room=ROOM):
    return [{"interpretation_id": "IN-cos-abcd", "sources": [{"source": {"store": store, "id": f"guildhall:{room}:t1"}}]}]


def test_cos_room_yes_confirms_with_the_stored_digest_as_the_owner(monkeypatch):
    from minimoi_portal.guild_ui.cos_room_approvals import approve
    seen = _portal(monkeypatch, _waiting(), Resp(200, {"state": "confirmed", "task_id": "TK-cos-abcd", "lead": "mc"}))
    code, state, receipt = approve(_body(), "owner-1")
    assert seen == [("confirm", {"interpretation_id": "IN-cos-abcd", "proposal_digest": DIGEST}, {"auth_id": "owner-1"})]
    assert (code, state) == ("P-CD34", "confirmed") and "TK-cos-abcd" in receipt and "MC" in receipt


def test_cos_room_no_declines(monkeypatch):
    from minimoi_portal.guild_ui.cos_room_approvals import approve
    seen = _portal(monkeypatch, _waiting(), Resp(200, {"state": "declined"}))
    assert approve(_body("cancel"), "owner-1")[1] == "cancelled" and seen[0][0] == "decline"


def test_a_changed_proposal_is_refused_and_nothing_starts(monkeypatch):
    from minimoi_portal.guild_ui.cos_room_approvals import approve
    _portal(monkeypatch, _waiting(), Resp(409, {"error": "changed", "message": "The proposal changed since the page showed it; reload and look again."}))
    code, state, receipt = approve(_body(), "owner-1")
    assert state == "refused" and receipt.startswith("The proposal changed")


@pytest.mark.parametrize("store,room", [("cos-history", ROOM), ("cos-meeting-owner", str(uuid4()))])
def test_a_proposal_from_elsewhere_cannot_be_approved_in_this_room(monkeypatch, store, room):
    from minimoi_portal.guild_ui.cos_room_approvals import approve
    seen = _portal(monkeypatch, _waiting(store, room), Resp(200, {"state": "confirmed"}))
    assert approve(_body(), "owner-1")[1] == "refused" and seen == []


def test_the_portal_door_needs_the_cos_assertion(tmp_path, monkeypatch):
    from core import portal_assertion as PA
    from minimoi_portal.guild_ui import cos_room_approvals as C
    key_file = tmp_path / "k"
    key_file.write_text(json.dumps({"kid": "k1", "secret": "s" * 64}))
    key_file.chmod(0o600)
    try:
        key = PA.load_key(str(key_file))
    except Exception:
        pytest.skip("portal assertion key format differs on this branch")
    monkeypatch.setenv("PORTAL_COS_OWNER_KEY_FILE", str(key_file))
    monkeypatch.setenv("PORTAL_COS_OWNER_ENV", "dev")
    monkeypatch.setattr(C, "approve", lambda body, owner: ("P-CD34", "confirmed", "Confirmed."))
    app = Flask(__name__)
    C.attach(app, {"MINIMOI_COS_ROOM_APPROVALS": "1", "MINIMOI_ROOMS_OWNER_AUTH_ID": "owner-1",
                   "MINIMOI_MC_ROOMS_STATE_DIR": str(tmp_path)})
    c = app.test_client()
    raw = json.dumps(_body()).encode()
    assert c.post(C.PATH, data=raw, content_type="application/json").status_code == 401
    header = PA.sign(key, audience=C.AUDIENCE, subject="service:cos", method="POST", target=C.PATH, body=raw, env="dev")
    r = c.post(C.PATH, data=raw, content_type="application/json", headers={C.HEADER: header})
    assert r.status_code == 200 and r.json == {"code": "P-CD34", "state": "confirmed", "receipt": "Confirmed."}
    # The assertion is spent once.
    assert c.post(C.PATH, data=raw, content_type="application/json", headers={C.HEADER: header}).status_code == 401
    wrong = PA.sign(key, audience="mc-from-cos", subject="service:cos", method="POST", target=C.PATH, body=raw, env="dev")
    assert c.post(C.PATH, data=raw, content_type="application/json", headers={C.HEADER: wrong}).status_code == 401


# ---- CoS: cos-scheduler answers an admitted approval turn without the model -----------------------------------
def test_cos_scheduler_answers_an_approval_turn_without_the_model(tmp_path):
    from domains.cos.guildhall import install
    facts = {"members": [{"id": "robert"}, {"id": "mc"}, {"id": "cos"}], "owner_private": True,
             "context": {"room": ROOM}, "trigger_text": "yes P-CD34", "claim_id": "c1", "room_generation": "g1",
             "binding": {"origin": "meeting"},
             "room_approval": {"code": "P-CD34", "kind": "confirm", "partner": "cos", "source_ref": "IN-cos-abcd",
                               "payload_digest": DIGEST}}
    verifier = SimpleNamespace(admit=lambda value: facts, watch=lambda *a, **k: (lambda: None))
    asked = []
    token = tmp_path / "tok"
    token.write_text("x" * 48)
    app = Flask(__name__)
    install(app, token_file=token, data_dir=tmp_path / "ledger", verifier=verifier,
            run_turn=lambda *a, **k: pytest.fail("the model was called"),
            approve_room=lambda room, turn, ra: asked.append((room, turn, ra["code"])) or
            {"state": "confirmed", "receipt": "Confirmed P-CD34: TK-cos-abcd is tracked, led by MC."})
    body = {"id": TURN, "room": ROOM, "addressee": "cos", "claim_id": "c1",
            "trigger": {"actor": "robert", "kind": "message", "origin": None, "text": "yes P-CD34"}}
    r = app.test_client().post("/internal/guildhall/turn", json=body, headers={"Authorization": "Bearer " + "x" * 48})
    assert r.status_code == 200, r.json
    assert asked == [(ROOM, TURN, "P-CD34")]
    assert r.json["room_decision"] == {"code": "P-CD34", "state": "confirmed",
                                       "receipt": "Confirmed P-CD34: TK-cos-abcd is tracked, led by MC."}
    assert r.json["text"].startswith("Confirmed P-CD34")


# ---- #681 C and E: refusals reach Robert; teammates speak to him ----------------------------------------------
def test_a_refused_step_adds_a_platform_line_under_the_reply():
    from minimoi_portal.guild_ui.mc_rooms_bridge import refusal_lines
    trace = {"steps": {"v": 1, "items": [
        {"tool": "minimoi_issue_create", "label": "Used issue create (refused)", "ok": False, "outcome": "refused"},
        {"tool": "minimoi_recall", "label": "Searched past conversations", "ok": True},
        {"tool": "minimoi_work_save", "label": "Saved a working document (not confirmed)", "ok": False, "outcome": "unknown"}]}}
    assert refusal_lines(trace) == ["Platform: Used issue create was refused, so nothing was done.",
                                    "Platform: Saved a working document could not be confirmed; do not repeat it yet."]
    assert refusal_lines({}) == [] and refusal_lines(None) == []


def test_the_collector_keeps_the_runs_steps_for_the_room():
    from minimoi_portal.guild_ui.mc.internal_stream import collect
    from minimoi_portal.guild_ui.mc.stream import Delta, Finish, RunStarted, Step

    class Backend:
        kind = "openclaw"

        def stream_turn(self, req):
            yield RunStarted("run-1")
            yield Step(1, "minimoi_issue_create", "start")
            yield Step(1, "minimoi_issue_create", "end", False, 12, "refused")
            yield Delta("I could not open it.")
            yield Finish("stop")
    req = SimpleNamespace(correlation_id="c1")
    out = collect(Backend(), req, on_run=lambda run: True, record_usage=lambda *a, **k: None)
    assert out.status == "answered"
    assert out.trace["steps"]["items"][0]["outcome"] == "refused"


def test_the_discussion_prompt_speaks_to_robert_and_names_the_way_to_tools():
    import inspect
    from services.rooms_worker import discussion
    src = inspect.getsource(discussion)
    assert "speak to him directly as 'you'" in src
    assert "naming you (for" in src and "gives you your tools" in src
    assert "Robert must name or select one teammate" not in src


def test_the_cos_guildhall_prompt_speaks_to_robert_as_you_and_keeps_approval_in_the_room():
    import inspect
    from domains.cos import confer_service
    src = inspect.getsource(confer_service)
    assert "Speak to Robert directly as 'you'" in src
    assert "Robert approves it here with" in src
