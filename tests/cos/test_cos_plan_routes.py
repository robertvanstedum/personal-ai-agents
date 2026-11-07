"""CoS on a subscription: opt-in on a host, no API route left in plan mode, per-conversation choice, run-bound evidence."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

from flask import Flask
import pytest

from core.model_routes import CATALOGUE_VAR, RouteRefused, load_catalogue
from domains.cos import model_route
from domains.cos.backends import openclaw_backend
from domains.cos.history import History
from domains.cos.history_api import create_blueprint
from test_cos_tool_listener import RUN, auth, live, rig, sealed  # noqa: F401  (rig is a fixture)
from test_openclaw_backend import StubResponse as FakeResponse, _backend

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docker/cos-agent-a/plan-access.cjs"
CONFIG = ROOT / "docker/cos-agent-a/openclaw.json"
ASTRA, SOL = "plan-openai-strong", "plan-openai-mid"
PLAN_ENV = {"COS_RUNTIME_MODEL_ACCESS": "plan", "COS_PLAN_MODEL": "openai/gpt-6-astra",
            "COS_PLAN_ROUTES": "openai/gpt-6-sol,openai/gpt-6-luna", "COS_PLAN_AUTH_PROFILE": "openai:robert"}


def _apply(tmp_path, env):
    target = tmp_path / "openclaw.json"
    shutil.copy(CONFIG, target)
    run = subprocess.run(["node", str(PLAN), str(target)], capture_output=True, text=True, env={"PATH": shutil.os.environ["PATH"], **env})
    return run, json.loads(target.read_text())


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
class TestRuntimeConfig:
    def test_plan_mode_leaves_no_api_route_and_no_fallback(self, tmp_path):
        run, config = _apply(tmp_path, PLAN_ENV)
        assert run.returncode == 0, run.stderr
        assert "models" not in config and config["auth"]["profiles"] == {"openai:robert": {"provider": "openai", "mode": "oauth"}}
        assert config["agents"]["defaults"]["thinkingDefault"] == "high"
        for agent in [config["agents"]["defaults"], *config["agents"]["entries"].values()]:
            assert agent["model"] == {"primary": "openai/gpt-6-astra", "fallbacks": []}
            assert agent["modelPolicy"]["allow"] == ["openai/gpt-6-astra", "openai/gpt-6-sol", "openai/gpt-6-luna"]

    def test_memory_search_is_keyword_only_on_purpose(self, tmp_path):
        # No CoS host holds an embedding key, and CoS's capped gateway key is refused on embeddings (scripts/staging/verify.sh).
        pinned = json.loads(CONFIG.read_text())
        assert pinned["memory"] == {"search": {"provider": "none"}}
        _, config = _apply(tmp_path, PLAN_ENV)
        assert config["memory"] == pinned["memory"]

    def test_plan_mode_keeps_each_agents_identity_tools_and_workspace(self, tmp_path):
        before = json.loads(CONFIG.read_text())["agents"]["entries"]
        _, config = _apply(tmp_path, PLAN_ENV)
        for name, entry in before.items():
            after = config["agents"]["entries"][name]
            for key in ("name", "workspace", "tools", "memorySearch", "agentDir"):
                assert after.get(key) == entry.get(key), (name, key)

    @pytest.mark.parametrize("change", [{"COS_PLAN_AUTH_PROFILE": ""}, {"COS_PLAN_AUTH_PROFILE": "anthropic:x"},
                                        {"COS_PLAN_MODEL": "minimoi-gateway/minimoi-cos-agent"}, {"COS_PLAN_ROUTES": "anthropic/claude-opus-5"},
                                        {"COS_RUNTIME_MODEL_ACCESS": "other"}])
    def test_anything_doubtful_refuses_to_start(self, tmp_path, change):
        run, config = _apply(tmp_path, {**PLAN_ENV, **change})
        assert run.returncode != 0 and config == json.loads(CONFIG.read_text())

    def test_a_host_that_did_not_ask_keeps_the_pinned_config_exactly(self, tmp_path):
        run, config = _apply(tmp_path, {})
        assert run.returncode == 0 and config == json.loads(CONFIG.read_text())


def test_gateway_hosts_never_run_the_plan_step():
    script = (ROOT / "docker/cos-agent-a/apply-config.sh").read_text()
    assert 'if [ "${COS_RUNTIME_MODEL_ACCESS:-gateway}" != gateway ]; then\n  node /opt/minimoi/cos-agent-a/plan-access.cjs' in script


# ── The header ──

def _reply():
    return FakeResponse(payload={"id": "chatcmpl_x", "model": "openclaw/cos-agent-a", "choices": [
        {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}]})


def _headers(backend_model, agent=None):
    sent = []

    def post(url, **kw):
        sent.append(kw["headers"])
        return _reply()
    token = openclaw_backend.turn_model.set(backend_model)
    try:
        _backend(post)._post_turn(messages=[{"role": "user", "content": "x"}], conversation_id="owner", agent_id=agent)
    finally:
        openclaw_backend.turn_model.reset(token)
    return sent[0]


def test_a_routed_turn_asks_for_its_model_and_an_unrouted_one_asks_for_nothing():
    assert _headers("openai/gpt-6-sol")["x-openclaw-model"] == "openai/gpt-6-sol"
    assert "x-openclaw-model" not in _headers(None)


def test_a_meeting_agent_never_takes_a_conversations_choice():
    assert "x-openclaw-model" not in _headers("openai/gpt-6-sol", agent="cos-meeting")


@pytest.mark.parametrize("bad", ["minimoi-gateway/minimoi-cos-agent", "openai/x y", "openai/a\nb"])
def test_a_bad_model_reference_sends_nothing(bad):
    with pytest.raises(openclaw_backend.AgentRuntimeError):
        _headers(bad)


# ── The choice ──

@pytest.fixture
def host(tmp_path, monkeypatch):
    """A host that opted in, with OpenAI's routes proved on CoS and A as its default."""
    catalogue = json.loads(json.dumps(load_catalogue()))
    for route in catalogue["routes"]:
        if route["route_id"] in (ASTRA, SOL):
            route["offered"]["cos"] = "available"
    catalogue["roles"]["cos-conversation"]["route"] = ASTRA
    path = tmp_path / "routes.json"
    path.write_text(json.dumps(catalogue))
    monkeypatch.setenv(CATALOGUE_VAR, str(path))
    history = History(tmp_path / "history", "dev")
    return history, history.ensure_default()["conversation_id"], path


def test_with_no_catalogue_nothing_changes(tmp_path, monkeypatch):
    monkeypatch.delenv(CATALOGUE_VAR, raising=False)
    assert model_route.for_turn(None, "any") is None and model_route.runtime_model(None) is None


def test_the_default_is_a_and_a_choice_applies_to_its_conversation_only(host):
    history, cid, _ = host
    other = history.create("Other")["conversation_id"]
    assert model_route.runtime_model(model_route.for_turn(history, cid)) == "openai/gpt-6-astra"
    model_route.choose(history, cid, SOL, 0)
    assert model_route.runtime_model(model_route.for_turn(history, cid)) == "openai/gpt-6-sol"
    assert model_route.runtime_model(model_route.for_turn(history, other)) == "openai/gpt-6-astra"


def test_a_stale_page_changes_nothing(host):
    history, cid, _ = host
    model_route.choose(history, cid, SOL, 0)
    with pytest.raises(model_route.RevisionConflict):
        model_route.choose(history, cid, ASTRA, 0)
    assert model_route.for_turn(history, cid)["route_id"] == SOL


def test_an_unproved_route_cannot_be_chosen_and_a_withdrawn_one_refuses_the_turn(host):
    history, cid, path = host
    with pytest.raises(RouteRefused):
        model_route.choose(history, cid, "plan-openai-low", 0)
    model_route.choose(history, cid, SOL, 0)
    catalogue = json.loads(path.read_text())
    next(r for r in catalogue["routes"] if r["route_id"] == SOL)["offered"]["cos"] = "Sign-in expired"
    path.write_text(json.dumps(catalogue))
    with pytest.raises(RouteRefused, match="Sign-in expired"):
        model_route.for_turn(history, cid)


def test_a_broken_catalogue_refuses_the_turn(host):
    history, cid, path = host
    path.write_text("{ not json")
    with pytest.raises(RouteRefused):
        model_route.for_turn(history, cid)


def test_a_receipt_keeps_what_was_selected_and_what_was_shown_apart(host):
    history, cid, _ = host
    route = model_route.for_turn(history, cid)
    model_route.record(history, cid, route, None)
    assert model_route.view_for(history, cid)["last_turn"]["served"] is None
    model_route.record(history, cid, route, {"provider": "openai", "model": "gpt-6-sol"})
    assert model_route.view_for(history, cid)["last_turn"]["served_matches"] is False
    model_route.record(history, cid, route, {"provider": "openai", "model": "gpt-6-astra"})
    assert model_route.view_for(history, cid)["last_turn"]["served_matches"] is True


def test_the_page_api_shows_and_changes_the_choice_for_the_owner_only(host):
    history, cid, _ = host
    owner = {"who": "robert"}
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(history, resolve_owner=lambda _: owner["who"]))
    client = app.test_client()
    url = f"/ui/history/conversations/{cid}/route"
    view = client.get(url).get_json()
    assert view["selected_route_id"] == ASTRA and view["selectable"] is True
    assert client.post(url, json={"route_id": SOL, "revision": 0}).get_json()["selected_route_id"] == SOL
    assert client.post(url, json={"route_id": ASTRA, "revision": 0}).status_code == 409
    assert client.post(url, json={"route_id": "plan-openai-low", "revision": 1}).status_code == 409
    assert client.post(url, json={"route_id": 5, "revision": 1}).status_code == 400
    assert client.get("/ui/history/conversations/nope/route").status_code in (400, 404)
    owner["who"] = None
    assert client.post(url, json={"route_id": ASTRA, "revision": 1}).status_code in (401, 403)


# ── The evidence ──

def _served(client, body, **kw):
    return client.post("/internal/cos/v1/served", json=body, headers=sealed(**kw))


def test_evidence_binds_to_the_exact_run_of_a_turn(rig):
    client, bindings, _ = rig
    live(bindings, turn="t1", run=1)
    live(bindings, turn="t2", run=2)
    assert _served(client, {"provider": "openai", "model": "gpt-6-astra", "api": "openai-chatgpt-responses"}, run=1).json["status"] == "kept"
    assert bindings.served_for_turn("t1") == {"provider": "openai", "model": "gpt-6-astra", "api": "openai-chatgpt-responses"}
    assert bindings.served_for_turn("t2") is None and bindings.served_for_turn("missing") is None


def test_the_last_attempt_of_a_run_is_the_one_kept(rig):
    client, bindings, _ = rig
    live(bindings, turn="t1", run=1)
    _served(client, {"provider": "openai", "model": "gpt-6-sol"})
    _served(client, {"provider": "openai", "model": "gpt-6-astra"})
    assert bindings.served_for_turn("t1")["model"] == "gpt-6-astra"


@pytest.mark.parametrize("body", [{"provider": "openai"}, {"provider": "openai", "model": "a b"}, {"provider": "x", "model": "y", "extra": "z"},
                                  {"provider": 1, "model": "y"}])
def test_strange_evidence_is_refused(rig, body):
    client, bindings, _ = rig
    live(bindings, turn="t1", run=1)
    assert _served(client, body).status_code == 400 and bindings.served_for_turn("t1") is None


def test_evidence_needs_the_listener_credential(rig):
    client, bindings, _ = rig
    live(bindings, turn="t1", run=1)
    assert client.post("/internal/cos/v1/served", json={"provider": "a", "model": "b"},
                       headers={**sealed(), **auth("x" * 40)}).status_code == 401


# ── A real page turn ──

class _Result:
    def __init__(self):
        self.reply, self.turn_id, self.conversation_id = "ok", "t", "c"

    def public_dict(self):
        return {"reply": self.reply, "turn_id": self.turn_id}


@pytest.fixture
def page(monkeypatch, tmp_path):
    import sys
    from test_history_wiring import _load
    catalogue = json.loads(json.dumps(load_catalogue(ROOT / "core/model_routes/catalogue.json")))
    for route in catalogue["routes"]:
        if route["route_id"] in (ASTRA, SOL):
            route["offered"]["cos"] = "available"
    catalogue["roles"]["cos-conversation"]["route"] = ASTRA
    path = tmp_path / "routes.json"
    path.write_text(json.dumps(catalogue))
    monkeypatch.setenv(CATALOGUE_VAR, str(path))
    cos = _load(monkeypatch, tmp_path, history_on=True)
    seen = []

    def turn(text, **kw):
        seen.append(openclaw_backend.turn_model.get())
        return _Result()
    monkeypatch.setattr(cos, "_process_confer_turn", turn)
    yield cos, seen, path
    sys.modules.pop("domains.cos.chief_of_staff", None)


def test_a_page_turn_takes_its_conversations_model_and_the_next_turn_on_the_thread_does_not_inherit_it(page):
    cos, seen, _ = page
    cid = cos._history.ensure_default()["conversation_id"]
    model_route.choose(cos._history, cid, SOL, 0)
    payload, status = cos._ui_turn({"text": "hello", "request_id": "r" * 32, "conversation_id": cid})
    assert status == 200 and seen == ["openai/gpt-6-sol"]
    assert openclaw_backend.turn_model.get() is None                    # what a Telegram or MC-lane turn would see next
    assert model_route.view_for(cos._history, cid)["last_turn"]["route_id"] == SOL
    assert model_route.view_for(cos._history, cid)["last_turn"]["served"] is None


def test_a_page_turn_on_a_withdrawn_route_sends_nothing(page):
    cos, seen, path = page
    cid = cos._history.ensure_default()["conversation_id"]
    model_route.choose(cos._history, cid, SOL, 0)
    catalogue = json.loads(path.read_text())
    next(r for r in catalogue["routes"] if r["route_id"] == SOL)["offered"]["cos"] = "Sign-in expired"
    path.write_text(json.dumps(catalogue))
    payload, status = cos._ui_turn({"text": "hello", "request_id": "s" * 32, "conversation_id": cid})
    assert status == 400 and "Sign-in expired" in payload["reply"] and seen == []
    assert openclaw_backend.turn_model.get() is None


def test_a_plan_turn_reports_the_runtimes_record_at_once_rather_than_waiting_for_a_gateway_receipt(page, monkeypatch):
    cos, _, _ = page
    waits = []
    monkeypatch.setattr(cos._routing_receipts, "wait_for", lambda *a, **k: waits.append(k) or None)
    monkeypatch.setattr(cos, "_bindings", type("B", (), {"served_for_turn": staticmethod(lambda t: {"provider": "openai", "model": "gpt-6-astra"})})())
    assert cos._get_routing_receipt("t")["served_model"] == "gpt-6-astra" and waits == []


def test_evidence_that_lands_during_the_wait_is_still_found(page, monkeypatch):
    cos, _, _ = page
    answers = iter([None, None, {"provider": "openai", "model": "gpt-6-astra"}])
    monkeypatch.setattr(cos._routing_receipts, "wait_for", lambda *a, **k: None)
    monkeypatch.setattr(cos, "_bindings", type("B", (), {"served_for_turn": staticmethod(lambda t: next(answers))})())
    assert cos._get_routing_receipt("t")["served_model"] == "gpt-6-astra"


def test_with_no_evidence_the_wait_is_bounded_and_says_so(page, monkeypatch):
    import time
    cos, _, _ = page
    monkeypatch.setattr(cos._routing_receipts, "wait_for", lambda *a, **k: time.sleep(k["timeout_seconds"]) or None)
    monkeypatch.setattr(cos, "_bindings", type("B", (), {"served_for_turn": staticmethod(lambda t: None)})())
    started = time.monotonic()
    assert cos._get_routing_receipt("t") is None and time.monotonic() - started < 2


def test_a_host_without_routes_keeps_its_gateway_wait_exactly(tmp_path, monkeypatch):
    import sys
    from test_history_wiring import _load
    monkeypatch.delenv(CATALOGUE_VAR, raising=False)
    cos = _load(monkeypatch, tmp_path, history_on=True)
    calls = []
    monkeypatch.setattr(cos._routing_receipts, "wait_for", lambda rid, timeout_seconds: calls.append(timeout_seconds) or {"served_model": "m"})
    assert cos._get_routing_receipt("t") == {"served_model": "m"} and calls == [1.0]
    sys.modules.pop("domains.cos.chief_of_staff", None)


def test_the_label_never_claims_a_gateway_where_routes_are_on(monkeypatch):
    monkeypatch.delenv("COS_AGENT_RUNTIME_MODEL_LABEL", raising=False)
    def built():
        return openclaw_backend.OpenClawBackend(write_memory=lambda e: True, dispatch_tool=lambda n, a: {}, gateway_url="http://x:1/v1",
                                                 gateway_token="t", agent_id="cos-agent-a", http_post=lambda *a, **k: None).model_label
    monkeypatch.delenv(CATALOGUE_VAR, raising=False)
    assert built() == "gateway-unresolved"
    monkeypatch.setenv(CATALOGUE_VAR, "/somewhere.json")
    assert built() == "no-evidence"


RUN_ID = "chatcmpl_00000001-0000-4000-8000-000000000000"


class _Stream:
    """What the runtime streams on a plan route: no provider named anywhere in it."""

    def __init__(self, text=""):
        base = {"id": RUN_ID, "object": "chat.completion.chunk", "model": "openclaw/cos-agent-a"}
        self.chunks = [{**base, "choices": [{"delta": {"role": "assistant"}}]},
                       *([{**base, "choices": [{"delta": {"content": text}}]}] if text else []),
                       {**base, "choices": [{"delta": {}, "finish_reason": "length"}]}]

    def iter_lines(self, **_):
        for chunk in self.chunks:
            yield "data: " + json.dumps(chunk)
        yield "data: [DONE]"


def _through_the_backend(monkeypatch, text):
    """The real stream reader, then the real reply path: the same code every provider's turn goes through."""
    backend = _backend(lambda *a, **k: None)
    payload = backend._read_stream(_Stream(text), lambda run: None)
    monkeypatch.setattr(backend, "_call_backend_payload", lambda *a: payload)
    context = {}
    return backend.call_backend("hello", context, {}), context


def test_an_output_limit_with_no_text_tells_robert_on_a_plan_route_too(monkeypatch):
    reply, context = _through_the_backend(monkeypatch, "")
    assert reply == openclaw_backend._EMPTY_OUTPUT_NOTICE
    assert context["completion_evidence"] == {"state": "cut_off", "reason": "output_limit", "source": "openclaw_connector"}


def test_an_output_limit_after_some_text_keeps_the_text_and_is_marked_cut_off(monkeypatch):
    reply, context = _through_the_backend(monkeypatch, "The first half")
    assert reply.startswith("The first half") and context["completion_evidence"]["state"] == "cut_off"


# ── Through the real portal ──

def test_the_pickers_write_needs_the_portals_csrf_and_is_forwarded_signed(monkeypatch):
    pytest.importorskip("minimoi_portal.app")
    import minimoi_portal.app as portal_app
    import minimoi_portal.config as cfg
    monkeypatch.setattr(portal_app, "_cos_write_guard_on", lambda: True)
    forwarded = []
    monkeypatch.setattr(portal_app._proxy, "proxy_to", lambda *a, **k: forwarded.append((a, k)) or ("ok", 200))
    client = portal_app.app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = {"username": "robert", "tier": "owner", "display_name": "Robert", "auth_id": 2}
        sess[portal_app.COS_CSRF_SESSION_KEY] = "w" * 43
    path = "/app/cos/ui/history/conversations/cosc-abc/route"
    body = {"route_id": SOL, "revision": 0}
    assert client.post(path, json=body).status_code == 403 and forwarded == []
    good = {"X-CSRF-Token": "w" * 43, "Origin": cfg.BASE_URL.rstrip("/"), "Sec-Fetch-Site": "same-origin"}
    assert client.post(path, json=body, headers=good).status_code == 200
    assert forwarded[0][1]["verified_cos_voice_write"] is True


# ── The page script, in a fake browser ──

@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_the_picker_script():
    run = subprocess.run(["node", str(ROOT / "tests/cos/cos_route_picker.test.cjs")], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr


# ── Plan failover, Robert's decision of 6 Nov 2026: the seven rules through the real adapter and binding store ──

from core.model_routes import served_receipt, snapshot, switched_route  # noqa: E402
from domains.cos.binding import BindingStore, NotBound, TurnBinder  # noqa: E402

RUN_A, RUN_B = "chatcmpl_00000001-0000-4000-8000-000000000000", "chatcmpl_00000002-0000-4000-8000-000000000000"
TURN = "11111111-1111-4111-8111-111111111111"
CHUNK = {"object": "chat.completion.chunk", "model": "openclaw/cos-agent-a"}


class _Sse:
    status_code = 200
    text = ""

    def __init__(self, run, *events):
        self.lines = ["data: " + json.dumps({**CHUNK, "id": run, "choices": [{"delta": {"role": "assistant"}}]})]
        self.lines += ["data: " + json.dumps(e if "error" in e else {**CHUNK, "id": run, "choices": [e]}) for e in events]
        self.lines.append("data: [DONE]")

    def iter_lines(self, **_):
        return iter(self.lines)


def _error(kind, message="x"):
    return {"error": {"type": kind, "message": message}}


def _ok(run=RUN_B):
    return _Sse(run, {"delta": {"content": "Answer."}}, {"delta": {}, "finish_reason": "stop"})


def _cos_route(*, failover=True):
    catalogue = json.loads(json.dumps(load_catalogue(ROOT / "core/model_routes/catalogue.json")))
    astra = next(r for r in catalogue["routes"] if r["route_id"] == ASTRA)
    astra["offered"]["cos"] = "available"
    if not failover:
        astra.pop("failover")
    catalogue["roles"]["cos-conversation"]["route"] = ASTRA
    return snapshot(catalogue, "cos-conversation")


class _Failover:
    def __init__(self, tmp_path, monkeypatch, *answers, route=None, binder=True):
        monkeypatch.setattr(openclaw_backend, "_TOOL_REPORT_GRACE_S", 0)
        self.store = BindingStore(tmp_path / "b" / "b.sqlite3", "dev")
        self.store.start_epoch("e1")
        self.store.register_instance("a" * 32)
        self.posts, self.answers = [], list(answers)
        self.backend = _backend(self._post, binder=None)
        self.backend._binder = TurnBinder(self.store) if binder else None
        self.state = {"route": route or _cos_route()}
        self.tokens = (openclaw_backend.turn_route.set(self.state), openclaw_backend.turn_model.set("openai/gpt-6-astra"))
        self.context = {"confer": {"conversation_id": "c", "receipt_id": TURN},
                        "binding": {"origin": "owner", "principal": "robert", "owner_request_id": "r"}}

    def _post(self, url, headers=None, json=None, **kw):
        self.posts.append({"model": headers.get("x-openclaw-model"), "user": json["user"], "messages": json["messages"]})
        answer = self.answers.pop(0)
        return answer(self) if callable(answer) else answer

    def turn(self):
        return self.backend.call_backend("hello", self.context, {})

    def close(self):
        openclaw_backend.turn_route.reset(self.tokens[0])
        openclaw_backend.turn_model.reset(self.tokens[1])


@pytest.fixture
def failover(tmp_path, monkeypatch):
    made = []

    def build(*answers, **kw):
        made.append(_Failover(tmp_path, monkeypatch, *answers, **kw))
        return made[-1]
    yield build
    for one in made:
        one.close()


@pytest.mark.parametrize("kind", ["rate_limit_error", "insufficient_quota", "authentication_error"])
def test_rule_1_each_declared_signal_moves_the_turn(failover, kind):
    f = failover(_Sse(RUN_A, _error(kind)), _ok())
    reply = f.turn()
    assert [p["model"] for p in f.posts] == ["openai/gpt-6-astra", "minimoi-gateway/minimoi-openai-strong"]
    assert reply.startswith("Answer.") and "the API" in reply and f.state["switched"]["route_id"] == "api-openai-strong"


@pytest.mark.parametrize("error", [_error("api_error", "rate limit reached"), _error("permission_error"), _error("invalid_request_error"), {"error": {"message": "internal error"}}])
def test_rule_1_anything_else_never_switches_and_reads_as_it_always_did(failover, error):
    f = failover(_Sse(RUN_A, error), _ok())
    with pytest.raises(openclaw_backend.AgentRuntimeError) as failed:
        f.turn()
    assert "invalid response" in str(failed.value) and len(f.posts) == 1 and "switched" not in f.state


def test_rule_1_a_non_200_never_switches(failover):
    f = failover(FakeResponse(status_code=429, text="too many"), _ok())
    with pytest.raises(openclaw_backend.AgentRuntimeError) as failed:
        f.turn()
    assert "HTTP 429" in str(failed.value) and len(f.posts) == 1


@pytest.mark.parametrize("report", ["calls", "native", "served"])
def test_rule_2_any_report_of_activity_on_the_run_stops_the_switch(failover, report):
    def down(f):
        def lines():
            yield _Sse(RUN_A).lines[0]
            f.store.attach_run(TURN, RUN_A)
            if report == "native":
                f.store.log_native(run_id=RUN_A, tool_call_id="c1", tool="exec", target="x")
            elif report == "served":
                f.store.record_served(run_id=RUN_A, fields={"provider": "openai", "model": "gpt-6-astra"})
            else:
                f.store._db().execute("INSERT INTO calls VALUES(?,?,?,?,?,NULL,NULL)", (TURN, "c1", "cos_actions", "t", 1))
            yield "data: " + json.dumps(_error("rate_limit_error"))
        resp = _Sse(RUN_A)
        resp.iter_lines = lambda **_: lines()
        return resp
    f = failover(down, _ok())
    with pytest.raises(openclaw_backend.AgentRuntimeError) as failed:
        f.turn()
    assert "A tool may already have started" in str(failed.value) and len(f.posts) == 1 and "switched" not in f.state


def test_rule_2_text_already_received_stops_the_switch(failover):
    f = failover(_Sse(RUN_A, {"delta": {"content": "Part"}}, _error("rate_limit_error")), _ok())
    with pytest.raises(openclaw_backend.AgentRuntimeError, match="A tool may already have started"):
        f.turn()
    assert len(f.posts) == 1


def test_rule_2_no_binding_means_no_evidence_and_no_switch(failover):
    f = failover(_Sse(RUN_A, _error("rate_limit_error")), _ok())
    f.context.pop("binding")
    with pytest.raises(openclaw_backend.AgentRuntimeError, match="A tool may already have started"):
        f.turn()
    assert len(f.posts) == 1


def test_rule_2_a_turn_with_no_binder_at_all_does_not_stream_so_it_never_switches(failover):
    f = failover(FakeResponse(status_code=429, text="too many"), binder=False)
    with pytest.raises(openclaw_backend.AgentRuntimeError):
        f.turn()
    assert len(f.posts) == 1


def test_rule_2_the_api_run_takes_the_binding_and_a_late_call_from_the_plan_run_finds_none(failover):
    f = failover(_Sse(RUN_A, _error("rate_limit_error")), _ok())
    f.turn()
    assert f.context["tool_binding"] == "bound"
    assert f.store.trace(TURN)["binding"]["run_id"] == RUN_B
    with pytest.raises(NotBound):
        f.store.resolve(run_id=RUN_A, tool_call_id="c", instance_id="a" * 32, agent_id="cos-agent-a",
                        session_user=f.posts[0]["user"], wait_s=0)


def test_rule_2_the_store_will_not_move_a_run_that_acted_or_a_stopped_turn(failover):
    store = failover().store
    for turn, run in ((TURN, RUN_A), ("t2", "chatcmpl_00000003-0000-4000-8000-000000000000")):
        store.record(turn, principal="robert", origin="owner", scope="owner", audience="c", agent_entry="cos-agent-a",
                     session_user="u" + turn, lifetime_s=60)
        store.attach_run(turn, run)
    store.record_served(run_id=RUN_A, fields={"provider": "openai", "model": "gpt-6-astra"})
    assert store.quiet(TURN, RUN_A) is False and store.move_run(TURN, RUN_A, RUN_B) is False
    store.revoke("t2")
    assert store.move_run("t2", "chatcmpl_00000003-0000-4000-8000-000000000000", RUN_B) is False
    assert store.quiet("t2", RUN_A) is False


def test_rule_3_a_refused_api_attempt_says_both_unavailable(failover):
    f = failover(_Sse(RUN_A, _error("rate_limit_error")), FakeResponse(status_code=400, text="model not allowed"))
    with pytest.raises(openclaw_backend.AgentRuntimeError, match="both unavailable"):
        f.turn()


def test_rule_3_the_cap_reached_says_both_unavailable(failover):
    f = failover(_Sse(RUN_A, _error("rate_limit_error")), _Sse(RUN_B, _error("api_error", "Budget has been exceeded")))
    with pytest.raises(openclaw_backend.AgentRuntimeError, match="both unavailable"):
        f.turn()


def test_rule_4_the_words_follow_the_evidence_and_the_receipt_says_api(failover):
    def answer(f):
        f.store.record_served(run_id=RUN_B, fields={"provider": "minimoi-gateway", "model": "minimoi-openai-strong"})
        return _ok()
    f = failover(_Sse(RUN_A, _error("insufficient_quota")), answer)
    reply = f.turn()
    assert "quota is used up" in reply and "ran on the API: minimoi-openai-strong" in reply and "tries the plan again" in reply
    receipt = served_receipt(f.state["switched"], f.store.served_for_turn(TURN))
    assert (receipt["route_id"], receipt["billing_label"], receipt["served_matches"]) == ("api-openai-strong", "API", True)
    assert receipt["switched_from"] == ASTRA


def test_rule_4_with_no_evidence_it_says_sent_not_ran(failover):
    f = failover(_Sse(RUN_A, _error("rate_limit_error")), _ok())
    assert "was sent to the API route" in f.turn()


def test_rule_5_the_same_session_and_messages_go_to_the_api_route(failover):
    f = failover(_Sse(RUN_A, _error("rate_limit_error")), _ok())
    f.turn()
    assert f.posts[0]["user"] == f.posts[1]["user"] and f.posts[0]["messages"] == f.posts[1]["messages"]


def test_rule_5_the_choice_stays_the_receipt_says_api_and_the_next_turn_tries_the_plan(page):
    cos, seen, _ = page
    cid = cos._history.ensure_default()["conversation_id"]

    def switch(text, **kw):
        seen.append(openclaw_backend.turn_model.get())
        state = openclaw_backend.turn_route.get()
        state["switched"] = switched_route(state["route"], "rate_limit")
        return _Result()
    plain = cos._process_confer_turn
    cos._process_confer_turn = switch
    assert cos._ui_turn({"text": "hello", "request_id": "r" * 32, "conversation_id": cid})[1] == 200
    shown = model_route.view_for(cos._history, cid)
    assert shown["last_turn"]["route_id"] == "api-openai-strong" and shown["last_turn"]["billing_label"] == "API"
    assert shown["last_turn"]["switched_from"] == ASTRA and shown["selected_route_id"] == ASTRA and shown["revision"] == 0
    assert openclaw_backend.turn_route.get() is None
    cos._process_confer_turn = plain
    assert cos._ui_turn({"text": "again", "request_id": "s" * 32, "conversation_id": cid})[1] == 200
    assert seen == ["openai/gpt-6-astra", "openai/gpt-6-astra"]
    assert model_route.view_for(cos._history, cid)["last_turn"]["route_id"] == ASTRA


def test_rule_6_no_declared_target_means_no_switch_and_it_says_so(failover):
    f = failover(_Sse(RUN_A, _error("rate_limit_error")), _ok(), route=_cos_route(failover=False))
    with pytest.raises(openclaw_backend.AgentRuntimeError, match="No API route is declared"):
        f.turn()
    assert len(f.posts) == 1


def test_rule_6_a_turn_with_no_route_never_switches(failover):
    f = failover(_Sse(RUN_A, _error("rate_limit_error")), _ok())
    openclaw_backend.turn_route.set(None)
    with pytest.raises(openclaw_backend.AgentRuntimeError, match="No API route is declared"):
        f.turn()
    assert len(f.posts) == 1


def test_rule_7_a_failed_api_attempt_never_starts_a_third(failover):
    f = failover(_Sse(RUN_A, _error("rate_limit_error")), _Sse(RUN_B, _error("insufficient_quota")), _ok())
    with pytest.raises(openclaw_backend.AgentRuntimeError, match="both unavailable"):
        f.turn()
    assert [p["model"] for p in f.posts] == ["openai/gpt-6-astra", "minimoi-gateway/minimoi-openai-strong"]


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
class TestFailoverRuntimeConfig:
    REF = "minimoi-gateway/minimoi-openai-strong"

    def test_rule_3_with_its_own_capped_key_the_target_is_allowed_never_primary_never_a_config_fallback(self, tmp_path):
        run, config = _apply(tmp_path, {**PLAN_ENV, "COS_FAILOVER_ROUTES": self.REF, "COS_GATEWAY_KEY_CAPPED": "1"})
        assert run.returncode == 0, run.stderr
        assert config["models"]["providers"]["minimoi-gateway"]["apiKey"] == "${MINIMOI_MODEL_GATEWAY_KEY}"
        for agent in [config["agents"]["defaults"], *config["agents"]["entries"].values()]:
            assert agent["model"] == {"primary": "openai/gpt-6-astra", "fallbacks": []} and self.REF in agent["modelPolicy"]["allow"]

    def test_rule_3_a_capped_key_file_is_proof(self, tmp_path):
        key = tmp_path / "key"
        key.write_text("sk-" + "a" * 22 + "\n")
        run, config = _apply(tmp_path, {**PLAN_ENV, "COS_FAILOVER_ROUTES": self.REF, "COS_GATEWAY_KEY_FILE": str(key)})
        assert run.returncode == 0 and self.REF in config["agents"]["defaults"]["modelPolicy"]["allow"]

    def test_rule_3_no_proof_of_a_capped_key_means_no_target_and_cos_still_starts(self, tmp_path):
        run, config = _apply(tmp_path, {**PLAN_ENV, "COS_FAILOVER_ROUTES": self.REF, "COS_GATEWAY_KEY_FILE": str(tmp_path / "none")})
        assert run.returncode == 0 and "models" not in config
        assert self.REF not in config["agents"]["defaults"]["modelPolicy"]["allow"]

    @pytest.mark.parametrize("bad", ["minimoi-gateway/minimoi-xai-low", "minimoi-gateway/minimoi-anthropic-strong", "openai/gpt-6-astra", "minimoi-gateway/minimoi-cos-agent"])
    def test_rule_6_only_openais_api_tiers_may_be_named(self, tmp_path, bad):
        assert _apply(tmp_path, {**PLAN_ENV, "COS_FAILOVER_ROUTES": bad, "COS_GATEWAY_KEY_CAPPED": "1"})[0].returncode != 0

    def test_a_gateway_host_ignores_it(self, tmp_path):
        run, config = _apply(tmp_path, {"COS_FAILOVER_ROUTES": self.REF, "COS_GATEWAY_KEY_CAPPED": "1"})
        assert run.returncode == 0 and config == json.loads(CONFIG.read_text())
