"""Actual Records store, synthetic runtime double: NOT live H1 evidence."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_cos_records_bridge import bridge
from cos_records_bridge import RoomBridgeError
from cos_room_responder import TurnJournal
from cos_agent_responder import OpenClawMeetingModel, SyntheticSessionPolicy, respond


@pytest.fixture
def agent_setup(bridge, tmp_path):
    store, room, client, _, _ = bridge
    store.append("robert", "synthetic-question", room,
                 {"body": "Synthetic question: identify your role.", "context_class": "robert_source"})
    calls = []
    def model(data, request_id, action):
        calls.append((data, request_id, action))
        return {"text": "Synthetic double, not CoS. Source: " + data["records"][-1]["id"],
                "coordination_request_id": request_id, "openclaw_run_id": "chatcmpl_" + str(uuid4()),
                "agent_id": "cos-agent-a", "mode": "actual_agent_response"}
    return SimpleNamespace(store=store, room=room, client=client, model=model, calls=calls,
                           journal=TurnJournal(tmp_path / "actual-agent-journal"),
                           policy=SyntheticSessionPolicy([room]))


def turn(s, request_id=None, **overrides):
    args = dict(client=s.client, model=s.model, journal=s.journal,
                policy=s.policy, owner_authorized=True)
    args.update(overrides)
    return respond(s.room, request_id or str(uuid4()), **args)


def test_execution_and_receipt_identity_preserved_after_restart(agent_setup):
    s = agent_setup
    request_id = str(uuid4())
    first = turn(s, request_id)
    replay = turn(s, request_id, journal=TurnJournal(s.journal.root))
    assert first == replay and len(s.calls) == 1
    op = first["operation"]
    assert op["coordination_request_id"] == request_id
    assert len({request_id, op["openclaw_run_id"], op["receipt_id"]}) == 3
    event = s.store.room("robert", s.room)["events"][-1]
    body = json.loads(event["body"])
    assert event["actor"] == "cos-dev" and event["context_class"] == "agent_draft"
    assert body["source_record_ids"] and body["snapshot_sha256"]
    assert body["tool_policy_enforced"] is False


@pytest.mark.parametrize("gate", ["owner", "policy", "observer", "revoked", "paused"])
def test_preflight_denies_before_model(agent_setup, gate):
    s = agent_setup
    options = {}
    if gate == "owner": options["owner_authorized"] = False
    elif gate == "policy": options["policy"] = SyntheticSessionPolicy()
    elif gate == "paused": s.store.state("robert", "pause", s.room, dict(state="paused", version=1, checkpoint="Pause"))
    else: s.store.membership("robert", "deny", s.room, dict(actor="cos-dev", role="observer" if gate == "observer" else "remove"))
    with pytest.raises(RoomBridgeError): turn(s, **options)
    assert not s.calls


@pytest.mark.parametrize("change", ["message", "pause", "revoke", "observer"])
def test_server_rechecks_authority_and_context_after_inference(agent_setup, change):
    s = agent_setup
    def model(*args):
        result = s.model(*args)
        if change == "message": s.store.append("robert", "concurrent", s.room, dict(body="Synthetic newer context"))
        elif change == "pause": s.store.state("robert", "pause", s.room, dict(state="paused", version=1, checkpoint="Pause"))
        else: s.store.membership("robert", "deny", s.room, dict(actor="cos-dev", role="remove" if change == "revoke" else "observer"))
        return result
    with pytest.raises(RoomBridgeError): turn(s, model=model)
    assert not any(e["actor"] == "cos-dev" for e in s.store.room("robert", s.room)["events"])


def test_uncertain_inference_never_repeated_after_restart(agent_setup):
    s = agent_setup
    op = str(uuid4())
    def timeout(*args):
        s.calls.append(args)
        raise TimeoutError("Synthetic timeout")
    with pytest.raises(TimeoutError): turn(s, op, model=timeout)
    with pytest.raises(RoomBridgeError, match="uncertain"):
        turn(s, op, journal=TurnJournal(s.journal.root))
    assert len(s.calls) == 1


@pytest.mark.parametrize("committed", [False, True])
def test_write_uncertainty_reconciles_same_payload_without_inference(agent_setup, committed):
    s = agent_setup
    op = str(uuid4())
    original = s.client.request
    def unavailable(path, payload=None, *args, **kwargs):
        if payload is not None:
            if committed: original(path, payload, *args, **kwargs)
            raise RoomBridgeError("Synthetic write uncertainty")
        return original(path, payload, *args, **kwargs)
    s.client.request = unavailable
    with pytest.raises(RoomBridgeError): turn(s, op)
    s.client.request = original
    result = turn(s, op, journal=TurnJournal(s.journal.root))
    assert result["operation"]["status"] == "committed" and len(s.calls) == 1
    assert sum(e["actor"] == "cos-dev" for e in s.store.room("robert", s.room)["events"]) == 1


@pytest.mark.parametrize("field,value", [("coordination_request_id", "wrong"), ("agent_id", "gateway"),
                                        ("openclaw_run_id", "caller-prose"), ("mode", "platform_acknowledgement")])
def test_bad_evidence_not_written_or_retried(agent_setup, field, value):
    s = agent_setup
    op = str(uuid4())
    def model(*args):
        result = s.model(*args)
        result[field] = value
        return result
    with pytest.raises(RoomBridgeError, match="evidence"): turn(s, op, model=model)
    with pytest.raises(RoomBridgeError, match="uncertain"): turn(s, op)
    assert len(s.calls) == 1


def test_changed_action_cannot_reuse_receipt(agent_setup):
    s = agent_setup
    op = str(uuid4())
    turn(s, op)
    with pytest.raises(RoomBridgeError, match="receipt"): turn(s, op, action="brief")
    assert len(s.calls) == 1


def test_brief_has_source_snapshot_and_no_ordinary_chat_context():
    calls = []
    class Backend:
        def call_backend_with_evidence(self, prompt, context, policy):
            calls.append((json.loads(prompt), context, policy))
            return SimpleNamespace(text="Synthetic briefing", coordination_request_id=context["confer"]["receipt_id"],
                                   openclaw_run_id="chatcmpl_" + str(uuid4()), agent_id="cos-agent-a", mode="actual_agent_response")
    model = OpenClawMeetingModel(Backend())
    request_id, room_id = str(uuid4()), str(uuid4())
    model({"room_id": room_id, "records": []}, request_id, "brief")
    assert calls[0][0]["action"] == "brief"
    assert calls[0][1]["confer"]["conversation_id"] == f"records:{room_id}:{request_id}"
    assert set(calls[0][1]) == {"system_prompt", "confer"}
