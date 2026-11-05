"""B4 build step 3, Journey 1 backend: Robert's saved words -> CoS proposes through its bound tool -> the platform
records his words from history -> Robert confirms (owner-signed) -> request and task with MC as lead, exactly as
confirmed. Real history, binding store, listener and task service over HTTP; synthetic tokens and model."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from core.canonical_json import digest
from domains.cos.binding import BindingStore, TurnBinder
from domains.cos.history import History
from domains.cos.store.writer import StoreWriter
from domains.cos.task_tools import CosTaskTools
from domains.cos.tool_listener import create_app as listener_app
from services.partnership_tasks.client import TaskClient
from services.partnership_tasks.http_api import create_app as tasks_app
from tests.partnership.synth import Flow, cmd, ref, service

TOKEN = "t" * 40
RUN = "chatcmpl_{:08x}-0000-0000-0000-000000000000"
USER = "cos-confer:" + "0" * 32
SESSION = "agent:cos-agent-a:openai-user:" + USER


class Session:
    def __init__(self, app):
        self.client = app.test_client()
        self.trust_env = False

    def request(self, method, url, headers=None, data=None, timeout=None, allow_redirects=None):
        r = self.client.open(url.split("http://tasks:8772", 1)[1], method=method, headers=headers, data=data)
        return SimpleNamespace(status_code=r.status_code, json=lambda: json.loads(r.data or b"{}"))


@pytest.fixture
def rig(tmp_path):
    tasks = service(tmp_path / "tasks")
    session = Session(tasks_app(tasks))
    history = History(tmp_path / "conv", "dev")
    history.ensure_default()
    bindings = BindingStore(tmp_path / "b" / "b.sqlite3", "dev")
    bindings.start_epoch("e1")
    bindings.register_instance("a" * 32)
    tools = CosTaskTools(platform=TaskClient("http://tasks:8772", "tok-platform", session=session),
                         cos=TaskClient("http://tasks:8772", "tok-cos", session=session),
                         history=history, environment="synthetic")
    app = listener_app(bindings=bindings, writer=StoreWriter(tmp_path / "store", "dev"),
                       token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest(), wait_s=0, task_tools=tools)
    return tasks, history, bindings, app.test_client()


def owner_turn(history, bindings, text, n=1, origin="owner", with_history=True):
    conv = "cos-owner" if False else history.ensure_default()["conversation_id"]
    turn, _ = history.begin({"schema": "minimoi.cos.send/1", "conversation_id": conv,
                             "turn_id": f"t-{n:032x}", "text": text, "channel": "web"})
    binding = {"origin": origin, "principal": "robert", "owner_request_id": f"req-{n}"}
    if with_history:
        binding["history"] = {"conversation_id": conv, "turn_id": turn["turn_id"]}
    bid = TurnBinder(bindings).begin(turn_id=f"turn{n}", session_user=USER, agent_entry="cos-agent-a",
                                     conversation_id="owner", meeting_scope=None, binding=binding)
    assert bid and bindings.attach_run(bid, RUN.format(n))
    return conv, turn


def call(client, args, n=1, call_id="c1"):
    h = {"Authorization": "Bearer " + TOKEN, "X-CoS-Session": SESSION, "X-CoS-Agent": "cos-agent-a",
         "X-CoS-Run": RUN.format(n), "X-CoS-Tool-Call": call_id, "X-CoS-Instance": "a" * 32}
    return client.post("/internal/cos/v1/tools/cos_tasks", json={"arguments": args}, headers=h).json


PROPOSAL = {"operation": "propose", "outcome": "A three-item review checklist for the codebase map.",
            "lead": "mc", "holder": "mc", "done_when": "MC accepts a reviewed checklist."}


def test_journey_1_backend_from_saved_words_to_a_task_led_by_mc(rig):
    tasks, history, bindings, client = rig
    conv, turn = owner_turn(history, bindings, "Track a small test task for our codebase map. Ask MC to lead.")
    out = call(client, PROPOSAL)
    assert out["status"] == "done", out
    iid = out["data"]["interpretation_id"]
    interp = tasks.get("interpretation", iid, "tok-principal")
    source = tasks.get("source_event", interp["source_event_ids"][0], "tok-principal")
    assert source["content"] == "Track a small test task for our codebase map. Ask MC to lead."   # from history
    assert source["source"] == {"store": "cos-history", "id": f"{conv}:{turn['turn_id']}", "position": 1}
    assert interp["provenance"]["actor_seat"] == "cos" and interp["disposition"] == "proposed"
    # Robert confirms in the portal (owner session; signed in production by the portal, see test_http_api).
    f = Flow(tasks)
    f.ok(cmd("source_event.record", "source_event", "SE-cnf-0001",
             {"source": {"store": "portal-owner-action", "id": "nonce-1", "position": 1},
              "content": json.dumps({"action": "confirm", "interpretation_id": iid,
                                     "proposal_digest": digest(interp["proposed"])}), "correction_of": None}),
         "tok-principal")
    f.ok(cmd("interpretation.dispose", "interpretation", iid,
             {"disposition": "adopted", "authority": {"kind": "owner_confirmation", "ref": ref("task_service", "SE-cnf-0001")}},
             expected=f.rev("interpretation", iid)), "tok-principal")
    f.request("RQ-cnf-0001", source["source_event_id"], "Codebase-map checklist.", iid=iid, token="tok-principal")
    terms = interp["proposed"]["task"]
    f.ok(cmd("request.promote", "request", "RQ-cnf-0001", {"task_id": "TK-cnf-0001", "parent_ref": None, **terms},
             expected=f.rev("request", "RQ-cnf-0001")), "tok-principal")
    task = tasks.get("task", "TK-cnf-0001", "tok-principal")
    assert (task["lead"], task["holder"], task["state"]) == ("mc", "mc", "submitted")
    status = call(client, {"operation": "status", "task_id": "TK-cnf-0001"}, call_id="c2")
    assert status["data"]["lead"] == "mc" and status["data"]["state"] == "submitted"


def test_one_turn_gives_at_most_one_proposal(rig):
    tasks, history, bindings, client = rig
    owner_turn(history, bindings, "Track a test task.")
    first = call(client, PROPOSAL)
    assert call(client, PROPOSAL, call_id="c2")["data"]["replayed"] is True
    other = call(client, dict(PROPOSAL, outcome="Something else."), call_id="c3")
    assert other["code"] == "already_proposed" and other["data"]["interpretation_id"] == first["data"]["interpretation_id"]


@pytest.mark.parametrize("origin,with_history", [("service:telegram", False), ("service:mc", False),
                                                 ("owner", False)])
def test_only_a_saved_owner_turn_may_propose(rig, origin, with_history):
    tasks, history, bindings, client = rig
    owner_turn(history, bindings, "Relayed text, not Robert's own words.", origin=origin, with_history=with_history)
    assert call(client, PROPOSAL)["code"] == "propose_needs_an_owner_conversation_turn"


@pytest.mark.parametrize("args,code", [
    ({"operation": "propose", "outcome": "x", "lead": "claude-code", "holder": "mc", "done_when": "y"},
     "lead_and_holder_must_be"),
    ({"operation": "propose", "outcome": "x", "lead": "mc", "holder": "mc"}, "unexpected_or_missing_fields"),
    ({**PROPOSAL, "actor": "robert"}, "unexpected_or_missing_fields"),
    ({**PROPOSAL, "source_event_id": "SE-forged-0001"}, "unexpected_or_missing_fields"),
])
def test_bad_or_identity_bearing_arguments_are_refused(rig, args, code):
    tasks, history, bindings, client = rig
    owner_turn(history, bindings, "Track a test task.")
    assert call(client, args)["code"] == code


def test_a_meeting_turn_may_read_status_but_never_propose(rig):
    tasks, history, bindings, client = rig
    assert TurnBinder(bindings).begin(turn_id="m1", session_user=USER, agent_entry="cos-meeting", conversation_id="r",
                                      meeting_scope="owner_private",
                                      binding={"origin": "meeting", "principal": "robert", "audience": "room-1",
                                               "claim_id": "c1", "room_generation": "g1"})
    assert bindings.attach_run("m1", RUN.format(7))
    h = {"Authorization": "Bearer " + TOKEN, "X-CoS-Session": "agent:cos-meeting:openai-user:" + USER,
         "X-CoS-Agent": "cos-meeting", "X-CoS-Run": RUN.format(7), "X-CoS-Tool-Call": "m-1", "X-CoS-Instance": "a" * 32}
    out = client.post("/internal/cos/v1/tools/cos_tasks", json={"arguments": PROPOSAL}, headers=h).json
    assert out["code"] == "scope_read_only"
    h["X-CoS-Tool-Call"] = "m-2"
    out = client.post("/internal/cos/v1/tools/cos_tasks", json={"arguments": {"operation": "status",
                                                                               "task_id": "TK-none-0001"}}, headers=h).json
    assert out["code"] == "not_found"                      # a read, refused only because no such task exists
