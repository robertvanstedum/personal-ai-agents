"""F2 step 2: the bound CoS tool listener. Forged headers, another actor in the body, the old static bearer and
unsealed calls are refused; a live turn's call writes through actions v2 with its receipt linked to the turn."""
import hashlib
import json

import pytest

from domains.cos.binding import BindingStore
from domains.cos.store.writer import StoreWriter
from domains.cos.tool_listener import create_app

TOKEN = "t" * 40
RUN = "chatcmpl_{:08x}-0000-0000-0000-000000000000"
INST = "a" * 32
USER = "cos-confer:" + "0" * 32
SESSION = "agent:cos-agent-a:openai-user:" + USER


@pytest.fixture
def rig(tmp_path):
    bindings = BindingStore(tmp_path / "b" / "bindings.sqlite3", "dev")
    bindings.start_epoch("e1")
    writer = StoreWriter(tmp_path / "store", "dev")
    app = create_app(bindings=bindings, writer=writer, token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest(),
                     wait_s=0)
    client = app.test_client()
    assert client.post("/internal/cos/v1/instance", json={"instance": INST}, headers=auth()).status_code == 200
    return client, bindings, writer


def auth(token=TOKEN):
    return {"Authorization": "Bearer " + token}


def sealed(run=1, call="call-1", session=SESSION, agent="cos-agent-a", instance=INST):
    return {**auth(), "X-CoS-Session": session, "X-CoS-Agent": agent, "X-CoS-Run": RUN.format(run),
            "X-CoS-Tool-Call": call, "X-CoS-Instance": instance}


def live(bindings, turn="t1", run=1, scope="owner"):
    extra = {} if scope == "owner" else {"claim_id": "c1", "room_generation": "g1"}
    agent = "cos-agent-a" if scope == "owner" else "cos-meeting"
    assert bindings.record(turn, principal="robert", origin="owner" if scope == "owner" else "meeting", scope=scope,
                           audience="owner", agent_entry=agent, session_user=USER, lifetime_s=600,
                           owner_request_id="req-1", supersession_key="dev:owner", **extra)
    assert bindings.attach_run(turn, RUN.format(run))


def tool(client, args, **kw):
    return client.post("/internal/cos/v1/tools/cos_actions", json={"arguments": args}, headers=sealed(**kw)).json


def test_live_owner_call_writes_and_links_the_receipt(rig):
    client, bindings, writer = rig
    live(bindings)
    out = tool(client, {"operation": "open", "title": "Prepare deck"})
    assert out["status"] == "done", out
    row = out["data"]["row"]
    assert row["opened_by"] == "cos" and row["title_provenance"] == "agent_draft"
    trace = bindings.trace("t1")
    assert trace["calls"][0]["receipt_id"] == out["data"]["result"]["receipt_id"]
    again = tool(client, {"operation": "open", "title": "Prepare deck"})          # host retry of the same call
    assert again["data"]["result"]["replayed"] is True and len(writer.docs("cos.in_flight")) == 1


def test_unsealed_forged_or_wrong_identity_is_refused(rig):
    client, bindings, writer = rig
    live(bindings)
    assert tool(client, {"operation": "open", "title": "x"}, run=2)["code"] == "run_unattached"
    assert tool(client, {"operation": "open", "title": "x"}, session="agent:cos-agent-a:openai-user:guild-mc:" + "0" * 32)[
        "code"] == "session_invalid"
    assert tool(client, {"operation": "open", "title": "x"}, agent="mc-chat")["code"] == "session_invalid"
    assert tool(client, {"operation": "open", "title": "x"}, instance="b" * 32)["code"] == "runtime_replaced"
    r = client.post("/internal/cos/v1/tools/cos_actions", json={"arguments": {"operation": "open", "title": "x"}},
                    headers={**auth(), "X-Minimoi-Owner": "robert", "X-MC-Run": RUN.format(1)})
    assert r.json["code"] == "session_invalid"
    assert writer.docs("cos.in_flight") == []


def test_body_naming_another_actor_or_identity_is_refused(rig):
    client, bindings, writer = rig
    live(bindings)
    for extra in ({"actor": "robert"}, {"origin": "owner_turn"}, {"environment": "prod"}, {"turn_id": "t9"}):
        assert tool(client, {"operation": "open", "title": "x", **extra})["code"] == "unexpected_fields"
    r = client.post("/internal/cos/v1/tools/cos_actions", json={"arguments": {"operation": "list"}, "actor": "robert"},
                    headers=sealed())
    assert r.status_code == 400
    assert writer.docs("cos.in_flight") == []


def test_old_static_bearer_and_bad_requests(rig):
    client, bindings, _ = rig
    live(bindings)
    assert client.post("/internal/cos/v1/tools/cos_actions", json={"arguments": {}},
                       headers=sealed() | auth("actions-test-token")).status_code == 401
    assert client.post("/internal/cos-actions/v1/open", json={}, headers=auth()).status_code == 404
    assert client.post("/internal/cos/v1/tools/cos_actions", data="x", headers=sealed()).status_code == 415
    assert client.post("/internal/cos/v1/tools/cos_actions", data='{"arguments":{},"arguments":{}}',
                       headers=sealed() | {"Content-Type": "application/json"}).status_code == 400
    assert tool(client, {"title": "no op"})["code"] == "arguments_invalid"
    assert client.post("/internal/cos/v1/tools/cos_shell", json={"arguments": {}}, headers=sealed()).json[
        "code"] == "tool_unknown"


def test_meeting_turn_reads_but_cannot_write(rig):
    client, bindings, writer = rig
    live(bindings, "m1", 5, scope="meeting")
    assert tool(client, {"operation": "list"}, run=5, agent="cos-meeting",
                session="agent:cos-meeting:openai-user:" + USER)["status"] == "done"
    out = tool(client, {"operation": "open", "title": "peer asked"}, run=5, agent="cos-meeting",
               session="agent:cos-meeting:openai-user:" + USER)
    assert out["status"] == "refused" and out["code"] == "scope_read_only"
    assert writer.docs("cos.in_flight") == []


def test_stopped_turn_and_quota(rig):
    client, bindings, _ = rig
    live(bindings)
    assert tool(client, {"operation": "list"}, call="c0")["status"] == "done"
    bindings.revoke("t1")
    assert tool(client, {"operation": "list"}, call="c1")["code"] == "stopped"


def test_native_calls_are_logged_and_never_refused(rig):
    client, bindings, _ = rig
    live(bindings)
    r = client.post("/internal/cos/v1/native", json={"tool": "exec", "target": "libreoffice --convert deck.pptx"},
                    headers=sealed(call="n1"))
    assert r.json == {"status": "logged", "bound": True}
    r = client.post("/internal/cos/v1/native", json={"tool": "write", "target": "/x"}, headers=sealed(run=9, call="n2"))
    assert r.json == {"status": "logged", "bound": False}
    assert client.post("/internal/cos/v1/native", json={"tool": "exec"}, headers=auth("wrong" * 10)).status_code == 401
    native = bindings.trace("t1")["native_calls"]
    assert [(n["tool"], n["target"]) for n in native] == [("exec", "libreoffice --convert deck.pptx")]


def test_config_refuses_bad_token_digest_and_environment_mismatch(tmp_path):
    b = BindingStore(tmp_path / "b.sqlite3", "dev")
    with pytest.raises(ValueError):
        create_app(bindings=b, writer=StoreWriter(tmp_path / "s", "dev"), token_sha256="nope")
    with pytest.raises(ValueError):
        create_app(bindings=b, writer=StoreWriter(tmp_path / "s2", "prod"),
                   token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest())


def test_instance_announcement(rig):
    client, _, _ = rig
    assert client.post("/internal/cos/v1/instance", json={"instance": INST}, headers=auth()).status_code == 200
    assert client.post("/internal/cos/v1/instance", json={"instance": "b" * 32}, headers=auth()).status_code == 200
    assert client.post("/internal/cos/v1/instance", json={"instance": INST}, headers=auth()).json == {
        "error": "instance_retired"}
    assert client.post("/internal/cos/v1/instance", json={"instance": "x"}, headers=auth()).status_code == 409
    assert json.loads(client.post("/internal/cos/v1/instance", json={"instance": INST, "x": 1},
                                  headers=auth()).data)["error"] == "tool_request_invalid"
