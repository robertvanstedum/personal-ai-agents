"""F2 step 3: with a binder, the OpenClaw adapter streams, attaches the run from the opening chunk before the reply is
read, picks the agent entry from the meeting scope, and ends the binding in finally. Stop and supersession revoke."""
import json
from uuid import uuid4

import pytest
from flask import Flask

from domains.cos.backends.openclaw_backend import AgentRuntimeError, OpenClawBackend
from domains.cos.binding import BindingStore, TurnBinder

RUN = "chatcmpl_12345678-1234-4123-8123-123456789abc"


class Stream:
    def __init__(self, chunks, status=200, events=None):
        self.chunks, self.status_code, self.events = chunks, status, events
        self.closed = False

    def iter_lines(self, decode_unicode=True):
        for chunk in self.chunks:
            if self.events is not None:
                self.events.append(("read", chunk if isinstance(chunk, str) else chunk.get("id")))
            yield chunk if isinstance(chunk, str) else "data: " + json.dumps(chunk)
        yield "data: [DONE]"

    def close(self):
        self.closed = True


def chunks(text="Hello", model="openclaw/cos-agent-a", run=RUN, finish="stop", extra=None):
    first = {"id": run, "object": "chat.completion.chunk", "model": model,
             "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
    body = [{"id": run, "object": "chat.completion.chunk", "model": model,
             "choices": [{"index": 0, "delta": {"content": part}, "finish_reason": None}]} for part in text.split("|")]
    last = {"id": run, "object": "chat.completion.chunk", "model": model,
            "choices": [{"index": 0, "delta": extra or {}, "finish_reason": finish}]}
    return [first, *body, last]


@pytest.fixture
def store(tmp_path):
    s = BindingStore(tmp_path / "b" / "bindings.sqlite3", "dev")
    s.start_epoch("e1")
    s.register_instance("a" * 32)
    return s


def backend(store, stream, sent=None, events=None):
    def post(url, **kw):
        if sent is not None:
            sent.append(kw)
        stream.events = events
        return stream
    return OpenClawBackend(write_memory=lambda *a: None, dispatch_tool=lambda *a: None, gateway_url="http://gw/v1",
                           gateway_token="tok", http_post=post, binder=TurnBinder(store))


def ctx(binding=None, scope=None, receipt=None):
    c = {"system_prompt": "s", "confer": {"conversation_id": "owner", "receipt_id": receipt or str(uuid4())},
         "binding": binding}
    if scope is not None:
        c["meeting_scope"] = scope
    return c


OWNER = {"origin": "owner", "principal": "robert", "owner_request_id": "req-1"}


def test_owner_turn_streams_binds_attaches_before_reading_and_ends(store):
    sent, events = [], []
    stream = Stream(chunks("Hel|lo"))
    attaches = []
    b = backend(store, stream, sent, events)
    original = b._binder.attach
    b._binder.attach = lambda turn, run: (attaches.append(len(events)), original(turn, run))[1]
    c = ctx(OWNER)
    assert b.call_backend("hi", c, {}) == "Hello"
    assert sent[0]["json"]["stream"] is True and sent[0]["stream"] is True
    assert sent[0]["json"]["model"] == "openclaw/cos-agent-a"
    assert attaches == [1]                       # attached right after the first chunk, before the reply text
    turn = c["confer"]["receipt_id"]
    trace = store.trace(turn)["binding"]
    assert trace["run_id"] == RUN and trace["origin"] == "owner" and trace["revoked_reason"] == "turn_ended"
    assert c["tool_binding"] == "bound" and c["completion_evidence"]["state"] == "complete" and stream.closed


def test_no_verified_binding_means_unbound_but_the_turn_still_answers(store):
    for binding in (None, {}, {"origin": "robert"}, {"origin": "owner"}):
        c = ctx(binding)
        assert backend(store, Stream(chunks())).call_backend("hi", c, {}) == "Hello"
        assert c["tool_binding"] == "unbound"
        assert store.trace(c["confer"]["receipt_id"])["binding"] is None


def test_meeting_turns_use_cos_meeting_and_closed_rooms_are_never_bound(store):
    sent = []
    c = ctx({"origin": "meeting", "principal": "robert", "audience": "room-1", "claim_id": "c1",
             "room_generation": "g3"}, scope="owner_private")
    backend(store, Stream(chunks(model="openclaw/cos-meeting")), sent).call_backend("hi", c, {})
    assert sent[0]["json"]["model"] == "openclaw/cos-meeting"
    assert store.trace(c["confer"]["receipt_id"])["binding"]["scope"] == "meeting"
    c = ctx({"origin": "meeting", "principal": "robert", "audience": "r", "claim_id": "c", "room_generation": "g1"},
            scope="closed")
    backend(store, Stream(chunks(model="openclaw/cos-meeting")), sent).call_backend("hi", c, {})
    assert sent[1]["json"]["model"] == "openclaw/cos-meeting"
    assert store.trace(c["confer"]["receipt_id"])["binding"] is None
    c = ctx(OWNER, scope="owner_private")          # an owner origin never binds a meeting turn
    backend(store, Stream(chunks(model="openclaw/cos-meeting")), sent).call_backend("hi", c, {})
    assert store.trace(c["confer"]["receipt_id"])["binding"] is None


def test_stream_failures_end_the_binding_and_raise(store):
    for bad in ([{"object": "nope"}], ["data: {not json"], []):
        c = ctx(OWNER)
        with pytest.raises(AgentRuntimeError):
            backend(store, Stream(bad)).call_backend("hi", c, {})
        row = store.trace(c["confer"]["receipt_id"])["binding"]
        assert row is None or row["revoked_reason"] == "turn_ended"
    c = ctx(OWNER)
    mixed = chunks() + [{"id": "chatcmpl_00000000-0000-4000-8000-000000000000", "object": "chat.completion.chunk",
                         "choices": []}]
    with pytest.raises(AgentRuntimeError):
        backend(store, Stream(mixed)).call_backend("hi", c, {})


def test_tool_call_delta_or_wrong_model_is_not_trusted(store):
    c = ctx(OWNER)
    reply = backend(store, Stream(chunks(extra={"tool_calls": [{"id": "x"}]}))).call_backend("hi", c, {})
    assert "completion_evidence" not in c            # a tool-call delta makes the reply untrusted, as before
    c = ctx(OWNER)
    backend(store, Stream(chunks(model="openclaw/other", finish="stop", text="x"))).call_backend("hi", c, {})
    assert "completion_evidence" not in c            # another entry's reply is untrusted
    c = ctx(OWNER)
    backend(store, Stream(chunks(finish="length", text="partial"))).call_backend("hi", c, {})
    assert c["completion_evidence"]["state"] == "cut_off"
    assert reply


def test_supersession_a_newer_owner_turn_revokes_the_older_one(store):
    first = ctx(OWNER)
    store.record(first["confer"]["receipt_id"], principal="robert", origin="owner", scope="owner", audience="owner",
                 agent_entry="cos-agent-a", session_user="u", lifetime_s=600, supersession_key="dev:owner")
    backend(store, Stream(chunks(run="chatcmpl_22222222-2222-4222-8222-222222222222"))).call_backend("hi", ctx(OWNER), {})
    assert store.trace(first["confer"]["receipt_id"])["binding"]["revoked_reason"] == "superseded"


def test_guildhall_stop_route_revokes_with_the_service_token(tmp_path, store):
    from domains.cos.guildhall import install
    token = tmp_path / "token"
    token.write_text("x" * 48)
    app = Flask(__name__)
    install(app, token_file=token, data_dir=tmp_path / "ledger", run_turn=lambda *a, **k: None,
            stop_turn=lambda turn: store.revoke(turn, "meeting_stop"))
    client = app.test_client()
    turn = str(uuid4())
    assert client.post(f"/internal/guildhall/turns/{turn}/stop").status_code == 401
    r = client.post(f"/internal/guildhall/turns/{turn}/stop", headers={"Authorization": "Bearer " + "x" * 48})
    assert r.status_code == 202 and r.json == {"state": "tools_revoked"}
    assert not store.record(turn, principal="robert", origin="meeting", scope="meeting", audience="r",
                            agent_entry="cos-meeting", session_user="u", lifetime_s=600, claim_id="c",
                            room_generation="g1")          # the Stop before the binding applies from the start
    assert client.post("/internal/guildhall/turns/not-a-uuid/stop",
                       headers={"Authorization": "Bearer " + "x" * 48}).status_code == 400


def test_request_binding_comes_only_from_the_signed_assertion(monkeypatch, tmp_path):
    from test_history_wiring import _load
    cos = _load(monkeypatch, tmp_path, history_on=False)
    import domains.cos.owner_assertion as oa
    app = Flask(__name__)
    for subject, want in (("owner:robert", {"origin": "owner", "principal": "robert"}),
                          ("service:mc", {"origin": "service:mc", "principal": "robert"}),
                          ("service:telegram", {"origin": "service:telegram", "principal": "robert"}),
                          ("service:other", None), (None, None)):
        monkeypatch.setattr(oa, "verified_subject", lambda s=subject: s)
        with app.test_request_context("/chat", method="POST", headers={"X-Request-Id": "r1"}):
            got = cos._request_binding()
        assert (got and {k: got[k] for k in ("origin", "principal")}) == want or got == want
    assert cos._request_binding() is None                 # no request: no binding
