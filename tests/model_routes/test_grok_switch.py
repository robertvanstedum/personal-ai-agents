"""Grok's on/off switch (#466). Off everywhere as shipped; one explicit value turns each piece on; nothing ever falls onto it."""
import json
from pathlib import Path
import re
import shutil
import subprocess

from jinja2 import Template
import pytest
import yaml

from core.model_routes import (CATALOGUE_VAR, RouteRefused, check_selectable, conversation_role, load_catalogue, relay_routes, route_of,
                               snapshot, view)

ROOT = Path(__file__).resolve().parents[2]
GROK = "api-xai-low"
GROK_REF = "minimoi-gateway/minimoi-xai-low"
CAT = load_catalogue()
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


def offered(*agents):
    """The host's one line: the route's ``offered`` names the agents."""
    copy = json.loads(json.dumps(CAT))
    route_of(copy, GROK)["offered"] = {agent: "available" for agent in agents}
    return copy


# ── As shipped: off ──

def test_the_route_is_pay_per_use_on_an_existing_gateway_tier():
    route = route_of(CAT, GROK)
    assert route["billing_mode"] == "metered_api" and "pay per use" in route["label"]
    assert route["provider"] == "minimoi-gateway" and route["model"] == "minimoi-xai-low" and route["tier"] == "low"
    tiers = {m["model_name"] for m in yaml.safe_load((ROOT / "services/model_gateway/litellm.prod.yaml").read_text())["model_list"]}
    assert route["model"] in tiers


def test_as_shipped_grok_is_offered_to_no_one_and_read_by_no_role():
    assert route_of(CAT, GROK)["offered"] == {}
    assert all(role["route"] != GROK for role in CAT["roles"].values())


@pytest.mark.parametrize("agent", ["mc", "cos"])
def test_as_shipped_no_picker_shows_it_and_no_conversation_can_choose_it(agent):
    assert GROK not in [r["route_id"] for r in view(CAT, agent, {})["routes"]]
    with pytest.raises(RouteRefused) as refused:
        check_selectable(CAT, agent, GROK)
    assert refused.value.code == "route_unknown"


def test_as_shipped_the_relay_list_is_the_subscription_routes_and_their_failover_targets_and_never_grok():
    assert relay_routes(CAT) == {"plan-openai-strong": "openai/gpt-6-astra", "plan-openai-mid": "openai/gpt-6-sol",
                                 "plan-openai-low": "openai/gpt-6-luna", "plan-anthropic-strong": "anthropic/claude-opus-5",
                                 "plan-anthropic-mid": "anthropic/claude-sonnet-4-6",
                                 "api-openai-strong": "minimoi-gateway/minimoi-openai-strong",
                                 "api-openai-mid": "minimoi-gateway/minimoi-openai-mid",
                                 "api-openai-low": "minimoi-gateway/minimoi-openai-low",
                                 "api-anthropic-strong": "minimoi-gateway/minimoi-anthropic-strong",
                                 "api-anthropic-mid": "minimoi-gateway/minimoi-anthropic-mid"}


def test_no_failover_lands_on_grok_and_grok_declares_none():
    assert "failover" not in route_of(CAT, GROK) and route_of(CAT, GROK)["offered"] == {}
    assert all(r["failover"] != GROK and "xai" not in r["failover"] for r in CAT["routes"] if "failover" in r)


def test_a_reason_instead_of_available_keeps_it_out_of_the_relay():
    copy = json.loads(json.dumps(CAT))
    route_of(copy, GROK)["offered"] = {"mc": "Paused"}
    assert GROK not in relay_routes(copy)


# ── One line: on ──

@pytest.mark.parametrize("agent", ["mc", "cos"])
def test_offering_it_shows_it_labelled_as_api_billing_and_it_can_be_chosen(agent):
    copy = offered(agent)
    shown = {r["route_id"]: r for r in view(copy, agent, {})["routes"]}[GROK]
    assert shown["available"] and shown["billing_label"] == "API" and "pay per use" in shown["label"]
    assert check_selectable(copy, agent, GROK)["route_id"] == GROK


@pytest.mark.parametrize("agent, other", [("mc", "cos"), ("cos", "mc")])
def test_offering_it_to_one_agent_leaves_the_other_alone(agent, other):
    with pytest.raises(RouteRefused):
        check_selectable(offered(agent), other, GROK)


def test_it_does_not_become_any_tasks_default():
    copy = offered("mc", "cos")
    for role in copy["roles"]:
        assert snapshot(copy, role)["route_id"] != GROK


@pytest.mark.parametrize("agent", ["mc", "cos"])
def test_a_chosen_turn_overrides_the_runtime_and_shares_the_conversations_history(agent):
    chosen = snapshot(offered(agent), conversation_role(agent), {"routes": {agent: {"route_id": GROK, "revision": 1}}})
    assert chosen["route_id"] == GROK and chosen["runtime_override"] is True
    assert chosen["session_history"] == "shared" and chosen["billing_mode"] == "metered_api"


def test_the_relay_learns_it_only_once_offered_to_mc():
    assert relay_routes(offered("mc"))[GROK] == GROK_REF
    assert GROK not in relay_routes(offered("cos"))


@pytest.mark.parametrize("agent", ["mc", "cos"])
def test_turning_it_off_refuses_a_conversation_still_on_it_and_substitutes_nothing(agent):
    conv = {"routes": {agent: {"route_id": GROK, "revision": 1}}}
    with pytest.raises(RouteRefused) as refused:
        snapshot(CAT, conversation_role(agent), conv)
    assert refused.value.code == "route_unavailable" and "Nothing was sent" in refused.value.message


def test_a_cos_choice_applies_from_the_next_turn_to_that_conversation_only(tmp_path, monkeypatch):
    from domains.cos import model_route
    from domains.cos.history import History
    path = tmp_path / "routes.json"
    path.write_text(json.dumps(offered("cos")))
    monkeypatch.setenv(CATALOGUE_VAR, str(path))
    history = History(tmp_path / "history", "dev")
    cid, other = history.ensure_default()["conversation_id"], history.create("Other")["conversation_id"]
    fixed_for_the_running_turn = model_route.for_turn(history, cid)
    model_route.choose(history, cid, GROK, 0)
    assert model_route.runtime_model(fixed_for_the_running_turn) is None
    assert model_route.runtime_model(model_route.for_turn(history, cid)) == GROK_REF
    assert model_route.runtime_model(model_route.for_turn(history, other)) is None


def test_a_cos_turn_asks_for_the_tier_and_keeps_the_same_session():
    from domains.cos.backends import openclaw_backend
    import importlib.util
    spec = importlib.util.spec_from_file_location("cos_backend_rig", ROOT / "tests/cos/test_openclaw_backend.py")
    rig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rig)
    sent = []

    def post(url, **kw):
        sent.append(kw)
        return rig.StubResponse(payload={"id": "chatcmpl_x", "model": "openclaw/cos-agent-a", "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}]})
    for model in (None, GROK_REF):
        token = openclaw_backend.turn_model.set(model)
        try:
            rig._backend(post)._post_turn(messages=[{"role": "user", "content": "x"}], conversation_id="owner", agent_id=None)
        finally:
            openclaw_backend.turn_model.reset(token)
    assert "x-openclaw-model" not in sent[0]["headers"] and sent[1]["headers"]["x-openclaw-model"] == GROK_REF
    assert sent[0]["json"]["user"] == sent[1]["json"]["user"]


@pytest.mark.parametrize("bad", ["minimoi-gateway/minimoi-xai-pro", "minimoi-gateway/minimoi-cos-agent", "xai/grok-4.3", "minimoi-gateway/minimoi-xai-low\nx"])
def test_cos_refuses_any_other_gateway_reference(bad):
    from domains.cos.backends import openclaw_backend
    assert not openclaw_backend._MODEL_REF.fullmatch(bad)


# ── No fallback onto Grok, anywhere ──

GATEWAY_CONFIGS = sorted((ROOT / "services/model_gateway").glob("litellm*.yaml"))


def _fallback_targets(config):
    """Every (source, target) in any fallback setting of a gateway config."""
    pairs = []
    for section in ("router_settings", "litellm_settings"):
        for key, value in (config.get(section) or {}).items():
            if "fallback" not in key:
                continue
            for entry in value or []:
                if isinstance(entry, dict):
                    pairs += [(source, target) for source, targets in entry.items() for target in targets]
                else:
                    pairs.append((key, entry))
    return pairs


@pytest.mark.parametrize("path", GATEWAY_CONFIGS, ids=lambda p: p.name)
def test_no_gateway_fallback_lands_on_grok_and_no_grok_tier_falls_anywhere(path):
    config = yaml.safe_load(path.read_text())
    served = {m["model_name"]: m["litellm_params"]["model"] for m in config["model_list"]}
    pairs = _fallback_targets(config)
    assert pairs, "the check found no fallbacks to read"
    for source, target in pairs:
        assert not served[target].startswith("xai/"), f"{source} falls back onto {target} ({served[target]})"
        assert not source.startswith("minimoi-xai-"), f"{source} has a fallback"


def test_the_catalogue_has_no_fallback_field_at_all():
    assert "fallback" not in json.dumps(CAT)


def test_every_route_a_role_reads_as_shipped_is_not_grok():
    for name, role in CAT["roles"].items():
        assert "xai" not in route_of(CAT, role["route"])["model"], name


def _node(script, *args, env=None):
    run = subprocess.run(["node", "-e", script, *map(str, args)], capture_output=True, text=True, env={"PATH": shutil.os.environ["PATH"], **(env or {})})
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout) if run.stdout.strip() else None


# ── CoS's runtime ──

COS_PLAN = ROOT / "docker/cos-agent-a/plan-access.cjs"
COS_CONFIG = ROOT / "docker/cos-agent-a/openclaw.json"
COS_PLAN_ENV = {"COS_RUNTIME_MODEL_ACCESS": "plan", "COS_PLAN_MODEL": "openai/gpt-6-astra", "COS_PLAN_ROUTES": "openai/gpt-6-sol",
                "COS_PLAN_AUTH_PROFILE": "openai:robert"}


def _cos(tmp_path, env):
    target = tmp_path / "openclaw.json"
    shutil.copy(COS_CONFIG, target)
    run = subprocess.run(["node", str(COS_PLAN), str(target)], capture_output=True, text=True, env={"PATH": shutil.os.environ["PATH"], **env})
    return run, json.loads(target.read_text())


@needs_node
class TestCosRuntime:
    def test_unset_changes_nothing(self, tmp_path):
        run, config = _cos(tmp_path, {})
        assert run.returncode == 0 and config == json.loads(COS_CONFIG.read_text())

    def test_on_a_gateway_host_it_is_allowed_never_primary_never_fallback(self, tmp_path):
        run, config = _cos(tmp_path, {"COS_METERED_ROUTES": GROK_REF, "COS_GATEWAY_KEY_CAPPED": "1"})
        assert run.returncode == 0, run.stderr
        pinned = json.loads(COS_CONFIG.read_text())
        assert "minimoi-xai-low" in [m["id"] for m in config["models"]["providers"]["minimoi-gateway"]["models"]]
        for name, agent in [("defaults", config["agents"]["defaults"]), *config["agents"]["entries"].items()]:
            before = pinned["agents"]["defaults"] if name == "defaults" else pinned["agents"]["entries"][name]
            assert agent["model"] == before["model"] and not agent["model"].get("fallbacks")
            assert agent["modelPolicy"]["allow"] == [*before["modelPolicy"]["allow"], GROK_REF]

    def test_on_a_plan_host_it_is_the_only_api_route_and_never_primary_or_fallback(self, tmp_path):
        run, config = _cos(tmp_path, {**COS_PLAN_ENV, "COS_METERED_ROUTES": GROK_REF, "COS_GATEWAY_KEY_CAPPED": "1"})
        assert run.returncode == 0, run.stderr
        assert [m["id"] for m in config["models"]["providers"]["minimoi-gateway"]["models"]] == ["minimoi-xai-low"]
        for agent in [config["agents"]["defaults"], *config["agents"]["entries"].values()]:
            assert agent["model"] == {"primary": "openai/gpt-6-astra", "fallbacks": []}
            assert agent["modelPolicy"]["allow"] == ["openai/gpt-6-astra", "openai/gpt-6-sol", GROK_REF]

    @pytest.mark.parametrize("access", [{}, COS_PLAN_ENV])
    def test_without_a_capped_key_the_route_is_not_there_and_cos_still_starts(self, tmp_path, access):
        no_file = {"COS_GATEWAY_KEY_FILE": str(tmp_path / "none")}
        run, config = _cos(tmp_path, {**access, **no_file, "COS_METERED_ROUTES": GROK_REF})
        assert run.returncode == 0, run.stderr
        assert GROK_REF not in json.dumps(config)

    def test_a_key_file_holding_the_master_key_is_not_a_capped_key(self, tmp_path):
        master = "sk-" + "m" * 32
        key = tmp_path / "key"
        key.write_text(master + "\n")
        env = {**COS_PLAN_ENV, "COS_METERED_ROUTES": GROK_REF, "COS_GATEWAY_KEY_FILE": str(key), "MINIMOI_MODEL_GATEWAY_KEY": master}
        run, config = _cos(tmp_path, env)
        assert run.returncode == 0, run.stderr
        assert GROK_REF not in json.dumps(config)

    def test_a_plan_host_without_the_value_still_has_no_api_provider(self, tmp_path):
        run, config = _cos(tmp_path, COS_PLAN_ENV)
        assert run.returncode == 0 and "models" not in config

    @pytest.mark.parametrize("bad", ["xai/grok-4.3", "minimoi-gateway/minimoi-cos-agent", "minimoi-gateway/minimoi-xai-pro",
                                     f"{GROK_REF},{GROK_REF}"])
    def test_anything_else_refuses_to_start(self, tmp_path, bad):
        assert _cos(tmp_path, {"COS_METERED_ROUTES": bad})[0].returncode != 0

    def test_the_start_script_only_runs_it_when_named(self):
        script = (ROOT / "docker/cos-agent-a/apply-config.sh").read_text()
        assert '[ "${COS_RUNTIME_MODEL_ACCESS:-gateway}" = gateway ] && [ -n "${COS_METERED_ROUTES:-}" ]' in script


# ── MC's runtime ──

MC_ACCESS = ROOT / "docker/mc-agent-container/model-access.cjs"
MC_PLAN_ENV = {"MC_RUNTIME_MODEL_ACCESS": "plan", "MC_PLAN_MODEL": "openai/gpt-6-astra", "MC_PLAN_AUTH_PROFILE": "openai:owner",
               "MC_PLAN_ROUTES": "openai/gpt-6-sol"}
MC_ON = {**MC_PLAN_ENV, "MC_METERED_ROUTES": GROK_REF, "MC_MODEL_GATEWAY_KEY": "sk-synthetic-test-key-not-real"}
MERGE = "const merge=(i,l)=>{const O=v=>v&&typeof v==='object'&&!Array.isArray(v);if(!O(i)||!O(l))return i;const o={...l};for(const k of Object.keys(i))o[k]=merge(i[k],l[k]);return o;};"
BASE = ROOT / "docker/mc-agent-container/openclaw.json"
MC_LOAD = f"const p=require({json.dumps(str(MC_ACCESS))});const fs=require('fs');{MERGE}const base=JSON.parse(fs.readFileSync({json.dumps(str(BASE))}));"


@needs_node
class TestMcRuntime:
    def test_off_the_overlay_has_no_api_provider_and_a_gateway_key_is_still_refused(self):
        got = _node(MC_LOAD + "console.log(JSON.stringify(p.overlay(process.env)))", env=MC_PLAN_ENV)
        assert "models" not in got and got["agents"]["defaults"]["modelPolicy"]["allow"] == ["openai/gpt-6-astra", "openai/gpt-6-sol"]
        with pytest.raises(AssertionError):
            _node(MC_LOAD + "p.mode(process.env)", env={**MC_PLAN_ENV, "MC_MODEL_GATEWAY_KEY": "sk-synthetic-test-key-not-real"})

    def test_on_it_is_allowed_never_primary_never_fallback_and_validates(self):
        got = _node(MC_LOAD + "const o=p.overlay(process.env);p.mode(process.env);p.validate(merge(o,base),process.env);console.log(JSON.stringify(o))", env=MC_ON)
        assert list(got["models"]["providers"]) == ["minimoi-gateway"]
        assert got["models"]["providers"]["minimoi-gateway"]["apiKey"] == "${MC_MODEL_GATEWAY_KEY}"
        for agent in [got["agents"]["defaults"], *got["agents"]["entries"].values()]:
            assert agent["model"] == {"primary": "openai/gpt-6-astra", "fallbacks": []}
            assert agent["modelPolicy"]["allow"] == ["openai/gpt-6-astra", "openai/gpt-6-sol", GROK_REF]
            assert agent["models"][GROK_REF] == {"agentRuntime": {"id": "openclaw"}}

    @pytest.mark.parametrize("env", [
        {**MC_ON, "MC_MODEL_GATEWAY_KEY": ""},                                  # no key of its own
        {**MC_ON, "MC_METERED_ROUTES": "xai/grok-4.3"},                         # a raw provider reference
        {**MC_ON, "MC_METERED_ROUTES": "minimoi-gateway/minimoi-mc-agent"},     # not a Grok tier
        {**MC_ON, "MC_METERED_ROUTES": f"{GROK_REF},{GROK_REF}"},
        {**MC_ON, "MC_PLAN_MODEL": ""},                                         # the old Claude-plan shape has no default to keep
        {**MC_ON, "XAI_API_KEY": "synthetic"},                                  # a provider credential stays forbidden
        {"MC_RUNTIME_MODEL_ACCESS": "gateway", "MINIMOI_ENV": "dev", "MC_MODEL_GATEWAY_KEY": "sk-synthetic-test-key-not-real",
         "MC_METERED_ROUTES": GROK_REF},                                         # dev gateway mode has its own route
    ], ids=["no-key", "raw-ref", "not-grok", "duplicate", "no-default", "provider-key", "gateway-mode"])
    def test_a_wrong_setting_refuses_to_start(self, env):
        with pytest.raises(AssertionError):
            _node(MC_LOAD + "p.mode(process.env)", env=env)

    def test_with_it_on_another_provider_or_a_fallback_is_still_refused(self):
        for tamper in ("c.models.providers.x={}", "c.agents.entries['mc-chat'].model.fallbacks=['" + GROK_REF + "']",
                       "c.agents.defaults.model.primary='" + GROK_REF + "'"):
            with pytest.raises(AssertionError):
                _node(MC_LOAD + f"const c=merge(p.overlay(process.env),base);{tamper};p.validate(c,process.env)", env=MC_ON)

    def test_turning_it_off_removes_what_the_last_start_patched_in(self):
        script = MC_LOAD + """
          const on={...process.env,MC_METERED_ROUTES:%s,MC_MODEL_GATEWAY_KEY:'sk-synthetic-test-key-not-real'};
          const live=JSON.parse(JSON.stringify(merge(p.overlay(on),base)));
          let refused=false;try{p.validate(merge(p.overlay(process.env),live),process.env)}catch{refused=true}
          const patch=p.retire(live,process.env);
          const out=merge(p.overlay(process.env),live);p.validate(out,process.env);
          console.log(JSON.stringify({refused,patch,left:JSON.stringify(out).includes('minimoi-xai')}))""" % json.dumps(GROK_REF)
        got = _node(script, env=MC_PLAN_ENV)
        assert got["refused"] is True and got["left"] is False
        assert got["patch"]["models"]["providers"]["minimoi-gateway"] is None
        assert got["patch"]["agents"]["entries"]["mc-chat"]["models"][GROK_REF] is None

    @pytest.mark.parametrize("env", [MC_PLAN_ENV, MC_ON, {}], ids=["plan", "on", "legacy"])
    def test_nothing_to_retire_means_no_patch(self, env):
        assert _node(MC_LOAD + "console.log(JSON.stringify({r:p.retire({models:{},agents:{}},process.env)}))", env=env)["r"] is None

    def _start_merge(self, live, env):
        """The start script's own merge step, on a live config. Returns its exit status and the patch it wrote."""
        script = (ROOT / "docker/mc-agent-container/start.sh").read_text()
        step = re.search(r"node -e '\n(const fs=require\(\"fs\"\),\[img,live,policyFile\].*?)\n' \"\$IMAGE_DIR", script, re.S).group(1)
        patch = Path("/tmp/mc-config.patch.json")
        patch.unlink(missing_ok=True)
        run = subprocess.run(["node", "-e", step, str(BASE), str(live), str(MC_ACCESS)], capture_output=True, text=True,
                             env={"PATH": shutil.os.environ["PATH"], **env})
        written = json.loads(patch.read_text()) if patch.exists() else None
        patch.unlink(missing_ok=True)
        return run.returncode, written, run.stderr

    def test_the_start_merge_writes_the_removal_when_grok_was_on_and_is_now_off(self, tmp_path):
        live = tmp_path / "live.json"
        live.write_text(json.dumps(_node(MC_LOAD + "console.log(JSON.stringify(merge(p.overlay(process.env),base)))", env=MC_ON)))
        status, patch, stderr = self._start_merge(live, MC_PLAN_ENV)
        assert status == 10, stderr
        assert patch["models"]["providers"]["minimoi-gateway"] is None
        assert patch["agents"]["defaults"]["models"][GROK_REF] is None
        assert patch["agents"]["defaults"]["modelPolicy"]["allow"] == ["openai/gpt-6-astra", "openai/gpt-6-sol"]

    def test_the_start_merge_writes_no_removal_when_grok_was_never_on(self, tmp_path):
        live = tmp_path / "live.json"
        live.write_text(json.dumps(_node(MC_LOAD + "console.log(JSON.stringify(merge(p.overlay(process.env),base)))", env=MC_PLAN_ENV)))
        status, patch, stderr = self._start_merge(live, MC_PLAN_ENV)
        assert status in (0, 10), stderr
        assert "providers" not in patch.get("models", {}) and "minimoi-xai" not in json.dumps(patch)


# ── Curator ──

def test_curator_default_is_haiku_and_only_the_one_value_turns_on_grok(monkeypatch):
    from domains.curator.curator_rss_v2 import configured_ranking_model, resolve_scoring_mode
    monkeypatch.delenv("CURATOR_RANKING_MODEL", raising=False)
    assert configured_ranking_model() == "haiku"
    monkeypatch.setenv("CURATOR_RANKING_MODEL", "  ")
    assert configured_ranking_model() == "haiku"
    monkeypatch.setenv("CURATOR_RANKING_MODEL", "grok-4.3")
    assert resolve_scoring_mode(configured_ranking_model()) == "xai"


@pytest.mark.parametrize("bad", ["grok", "grok-4.7", "sonnet", "mechanical", "HAIKU"])
def test_curator_refuses_a_value_it_does_not_know(monkeypatch, bad):
    from domains.curator.curator_rss_v2 import configured_ranking_model
    monkeypatch.setenv("CURATOR_RANKING_MODEL", bad)
    with pytest.raises(ValueError, match="CURATOR_RANKING_MODEL"):
        configured_ranking_model()


@pytest.mark.parametrize("script", ["scripts/run_curator_cron_ec2.sh", "scripts/operations/run_curator_cron.sh"])
def test_both_crons_read_the_value_and_no_longer_name_a_model(script):
    text = (ROOT / script).read_text()
    assert "--model=configured --fallback --temperature=0.7" in text and "--model=haiku" not in text and "--model=grok" not in text


# ── German and Portuguese text ──

def _select(path, select_id):
    """The model picker's source, rendered with the switch off and on."""
    source = (ROOT / path).read_text()
    block = re.search(rf'<select id="{select_id}".*?</select>', source, re.S).group(0)
    return Template(block).render(grok_text=False), Template(block).render(grok_text=True)


def test_german_picker_off_is_the_paused_picker_byte_for_byte_and_on_selects_grok():
    off, on = _select("domains/german/templates/german_gesprache.html", "gesprache-model")
    assert off == ('<select id="gesprache-model" class="gesprache-model-select">\n'
                   '                <option value="claude" selected>Claude</option>\n'
                   '                <option value="openai">OpenAI</option>\n'
                   '              </select>')
    assert '<option value="grok" selected>Grok</option>' in on


def test_portuguese_picker_off_is_the_paused_picker_byte_for_byte_and_on_adds_grok_keeping_claude_selected():
    off, on = _select("domains/portuguese/templates/portuguese_conversas.html", "conversas-model")
    assert off == ('<select id="conversas-model" class="gesprache-model-select">\n'
                   '                <option value="claude" selected>Claude</option>\n'
                   '                <option value="openai">OpenAI</option>\n'
                   '              </select>')
    assert '<option value="grok">Grok</option>' in on and '<option value="claude" selected>' in on


def test_each_page_passes_its_own_switch():
    assert "grok_text=grok_text_on()" in (ROOT / "domains/german/html_server.py").read_text()
    assert 'os.environ.get("PORTUGUESE_GROK_TEXT", "").strip().lower() == "on"' in (ROOT / "domains/portuguese/html_server.py").read_text()


@pytest.fixture
def german_domain(german_client):
    import sys
    return sys.modules["german_domain"]


def test_german_providers_off_are_the_paused_list_and_on_put_grok_first(german_domain, monkeypatch):
    monkeypatch.delenv("GERMAN_GROK_TEXT", raising=False)
    assert german_domain._llm_providers() is german_domain._LLM_PROVIDERS
    assert [p["type"] for p in german_domain._LLM_PROVIDERS] == ["anthropic", "ollama"]
    for almost in ("", "off", "1", "true", "grok"):
        monkeypatch.setenv("GERMAN_GROK_TEXT", almost)
        assert german_domain._llm_providers() is german_domain._LLM_PROVIDERS
    monkeypatch.setenv("GERMAN_GROK_TEXT", "on")
    assert german_domain._llm_providers()[0] == {"name": "grok-fast", "type": "xai", "model": "grok-4.3"}
    assert [p["type"] for p in german_domain._llm_providers()[1:]] == ["anthropic", "ollama"]


def test_voice_is_untouched_by_the_switch():
    """The Grok Voice choice stays in both domains, and the gateway's voice roles are on Grok."""
    for path in ("domains/german/templates/german_gesprache.html", "domains/portuguese/templates/portuguese_conversas.html"):
        assert '<option value="xai">Grok Voice</option>' in (ROOT / path).read_text()
    config = yaml.safe_load((ROOT / "services/model_gateway/litellm.prod.yaml").read_text())
    served = {m["model_name"]: m["litellm_params"]["model"] for m in config["model_list"]}
    assert served["minimoi-voice-summary"] == served["minimoi-voice-intent"] == "xai/grok-4.3"
