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


def test_a_plan_turn_with_no_gateway_receipt_reports_the_runtimes_own_record(page, monkeypatch):
    cos, _, _ = page
    monkeypatch.setattr(cos._routing_receipts, "wait_for", lambda *a, **k: None)
    monkeypatch.setattr(cos, "_bindings", type("B", (), {"served_for_turn": staticmethod(lambda t: {"provider": "openai", "model": "gpt-6-astra"})})())
    assert cos._get_routing_receipt("t")["served_model"] == "gpt-6-astra"
    monkeypatch.setattr(cos, "_bindings", type("B", (), {"served_for_turn": staticmethod(lambda t: None)})())
    assert cos._get_routing_receipt("t") is None
