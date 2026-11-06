"""Dev's routes operator: Grok as a capped choice, plan-to-API failover by provider. Synthetic: no Docker, no host, no AWS, no model call."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts/staging"))
from core.model_routes import load_catalogue, relay_routes  # noqa: E402


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/staging/{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gs, D = _load("guarded_swap"), _load("beta_dev_deploy")
R = _load("beta_dev_routes")
payload = _load("beta_dev_routes_payload")
REAL_RUN = subprocess.run
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs node")
sha = lambda b: hashlib.sha256(b).hexdigest()
A = D.A
COS_KEY = "sk-cos-existing-key-0000000000"
MASTER = "sk-master-key-of-the-gateway-000"
TOOLS = "t" * 40
RELAY_START = {"plan-openai-strong": "openai/gpt-6-astra", "plan-openai-mid": "openai/gpt-6-sol", "plan-openai-low": "openai/gpt-6-luna",
               "plan-anthropic-strong": "anthropic/claude-opus-5", "plan-anthropic-mid": "anthropic/claude-sonnet-4-6"}


def dev_catalogue():
    """Dev's host copy as it stands: default A for both, older than the failover lines and the API routes."""
    cat = json.loads((ROOT / "core/model_routes/catalogue.json").read_text())
    cat["routes"] = [r for r in cat["routes"] if not r["route_id"].startswith("api-")]
    for route in cat["routes"]:
        route.pop("failover", None)
        if route["route_id"].startswith("plan-openai"):
            route["offered"]["cos"] = "available"
    cat["roles"]["cos-conversation"]["route"] = A
    return cat


# ── The catalogue ──

def test_embedded_routes_are_the_repo_catalogues():
    repo = {r["route_id"]: r for r in json.loads((ROOT / "core/model_routes/catalogue.json").read_text())["routes"]}
    for route_id, route in R.API_ROUTES.items():
        assert route == repo[route_id]


def test_the_catalogue_diff_is_exactly_as_stated():
    old = dev_catalogue()
    new, lines = R.plan_catalogue(old, ("openai",))
    assert lines == ["+ route api-openai-strong: minimoi-gateway/minimoi-openai-strong, metered_api, offered {}",
                     "+ route api-openai-mid: minimoi-gateway/minimoi-openai-mid, metered_api, offered {}",
                     "+ route api-openai-low: minimoi-gateway/minimoi-openai-low, metered_api, offered {}",
                     "+ route api-xai-low: minimoi-gateway/minimoi-xai-low, metered_api, offered {}",
                     "~ route plan-openai-strong: + failover: api-openai-strong",
                     "~ route plan-openai-mid: + failover: api-openai-mid",
                     "~ route plan-openai-low: + failover: api-openai-low",
                     '~ route api-xai-low: offered {} -> {"cos": "available", "mc": "available"}']
    # Undo exactly what the lines state, and the old file is back.
    undo = copy.deepcopy(new)
    undo["routes"] = [r for r in undo["routes"] if r["route_id"] not in ("api-openai-strong", "api-openai-mid", "api-openai-low", "api-xai-low")]
    for route in undo["routes"]:
        route.pop("failover", None)
    assert undo == old
    assert new["roles"] == old["roles"] and new["revision"] == old["revision"]
    grok = next(r for r in new["routes"] if r["route_id"] == "api-xai-low")
    assert grok["offered"] == {"mc": "available", "cos": "available"} and grok["billing_mode"] == "metered_api"
    diff = "\n".join(R.catalogue_diff(old, new))
    assert "plan-anthropic" not in diff and "api-anthropic" not in diff and '"roles"' not in diff


def test_the_new_catalogue_loads_in_the_real_code_and_the_relay_gains_only_the_targets(tmp_path):
    new, _ = R.plan_catalogue(dev_catalogue(), ("openai",))
    path = tmp_path / "c.json"
    path.write_text(R.catalogue_text(new))
    routes = relay_routes(load_catalogue(path))
    assert set(routes) == {*RELAY_START, "api-openai-strong", "api-openai-mid", "api-openai-low", "api-xai-low"}
    R.check_relay_routes(json.dumps(RELAY_START), json.dumps(routes), new, ("openai",))


def test_anthropic_failover_is_off_by_default_and_says_why():
    assert R.parse_failover(R.DEFAULT_FAILOVER) == ("openai",)
    new, _ = R.plan_catalogue(dev_catalogue(), R.parse_failover(R.DEFAULT_FAILOVER))
    claude = [r for r in new["routes"] if r["provider"] == "anthropic"]
    assert claude and all("failover" not in r for r in claude)
    assert not [r for r in new["routes"] if r["route_id"].startswith("api-anthropic")]
    assert "minimoi-anthropic" not in json.dumps(R.route_env(new))
    said = R.failover_statement(("openai",))
    assert "left OUT" in said and "unproved" in said and "--failover openai,anthropic" in said


def test_anthropic_failover_is_a_parameter_and_cos_still_holds_openai_only():
    new, _ = R.plan_catalogue(dev_catalogue(), R.parse_failover("openai,anthropic"))
    env = R.route_env(new)
    assert "minimoi-anthropic-strong" in env[R.MC]["MC_FAILOVER_ROUTES"] and "minimoi-anthropic" not in env[R.COS]["COS_FAILOVER_ROUTES"]
    assert next(r for r in new["routes"] if r["route_id"] == "plan-anthropic-mid")["failover"] == "api-anthropic-mid"


@pytest.mark.parametrize("providers", [("openai",), ("openai", "anthropic")])
def test_grok_is_never_a_failover_target_and_never_a_default(providers):
    new, _ = R.plan_catalogue(dev_catalogue(), providers)
    assert all("xai" not in str(r.get("failover") or "") for r in new["routes"])
    assert all(role["route"] != R.GROK_ROUTE for role in new["roles"].values())
    env = R.route_env(new)
    assert "xai" not in env[R.MC]["MC_FAILOVER_ROUTES"] and "xai" not in env[R.COS]["COS_FAILOVER_ROUTES"]
    assert env[R.MC]["MC_METERED_ROUTES"] == R.GROK_REF == env[R.COS]["COS_METERED_ROUTES"]


@pytest.mark.parametrize("bad", ["xai", "grok", "", "openai,openai", "openai,xai"])
def test_the_provider_list_refuses_anything_else(bad):
    with pytest.raises(R.Refused):
        R.parse_failover(bad)


def test_a_host_that_already_fails_over_onto_grok_or_a_role_on_grok_is_refused():
    onto = dev_catalogue()
    next(r for r in onto["routes"] if r["route_id"] == "plan-openai-low")["failover"] = "api-xai-low"
    with pytest.raises(R.Refused, match="fails over to api-xai-low"):
        R.plan_catalogue(onto, ("openai",))
    moved = dev_catalogue()
    moved["roles"]["mc-conversation"]["route"] = "api-xai-low"
    with pytest.raises(AssertionError):
        R.plan_catalogue(moved, ("openai",))


def test_a_host_with_a_claude_failover_line_is_refused_rather_than_silently_kept():
    cat = dev_catalogue()
    next(r for r in cat["routes"] if r["route_id"] == "plan-anthropic-mid")["failover"] = "api-anthropic-mid"
    with pytest.raises(R.Refused, match="which this plan does not choose"):
        R.plan_catalogue(cat, ("openai",))


def test_a_moved_default_is_refused():
    cat = dev_catalogue()
    cat["roles"]["cos-conversation"]["route"] = "plan-openai-mid"
    with pytest.raises(AssertionError, match="default route moved"):
        R.plan_catalogue(cat, ("openai",))


# ── The mc_keys.sh helper, the gateway's own check and the images' own policies, run for real ──

def test_keygen_comes_from_the_helper_with_this_exact_model_list_and_the_cap_by_variable():
    code = R.keygen_source("mc", ["minimoi-openai-low", "minimoi-xai-low"], ROOT / "scripts/staging/mc_keys.sh")
    assert '"models": ["minimoi-openai-low", "minimoi-xai-low"]' in code and 'os.environ["ROUTES_CAP"]' in code
    assert '"budget_duration": "30d"' in code and '"rpm_limit": 10' in code and "mc-routes-" in code
    compile(code, "keygen", "exec")
    assert "minimoi-anthropic" not in code
    assert "minimoi-cos-web-search" in R.cos_base_models(ROOT / "scripts/staging/mc_keys.sh")


def test_gateway_check_reads_a_real_gateway_config(tmp_path):
    code = R.GATEWAY_CHECK_PY.replace("/app/config.yaml", str(ROOT / "services/model_gateway/litellm.staging.yaml"))
    env = {"PATH": os.environ["PATH"], "ROUTES_WANT": json.dumps(["minimoi-openai-low", "minimoi-xai-low", "minimoi-none", "minimoi-cos-agent-xai-fast"]), "GATEWAY_OPENAI_API_KEY": "x"}
    run = REAL_RUN([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True)
    got = json.loads(run.stdout)
    assert got["missing"] == ["minimoi-none"] and got["no_provider_key"] == ["GATEWAY_XAI_API_KEY"]
    assert got["in_a_fallback"] == ["minimoi-cos-agent-xai-fast"] and got["key_database"] is False


@needs_node
def test_mc_policy_script_runs_against_the_real_policy_file():
    env = {"PATH": os.environ["PATH"], "ROUTES_POLICY_FILE": str(ROOT / "docker/mc-agent-container/model-access.cjs"), "MC_RUNTIME_MODEL_ACCESS": "plan", "MC_PLAN_MODEL": "openai/gpt-6-astra",
           "MC_PLAN_ROUTES": "openai/gpt-6-sol", "MC_PLAN_AUTH_PROFILE": "openai:owner", "MC_METERED_ROUTES": R.GROK_REF, "MC_FAILOVER_ROUTES": "minimoi-gateway/minimoi-openai-strong",
           "MC_MODEL_GATEWAY_KEY": R.PLACEHOLDER_KEY}
    got = json.loads(REAL_RUN(["node", "-e", R.MC_POLICY_JS], capture_output=True, text=True, env=env, check=True).stdout)
    assert got["primary"] == "openai/gpt-6-astra" and got["fallbacks"] == []
    assert got["allow"] == ["openai/gpt-6-astra", "openai/gpt-6-sol", R.GROK_REF, "minimoi-gateway/minimoi-openai-strong"]
    assert got["provider"]["apiKey"] == "${MC_MODEL_GATEWAY_KEY}" and R.PLACEHOLDER_KEY not in json.dumps(got)


@needs_node
def test_cos_policy_script_runs_against_the_real_policy_file():
    env = {"PATH": os.environ["PATH"], "ROUTES_POLICY_FILE": str(ROOT / "docker/cos-agent-a/plan-access.cjs"), "ROUTES_PINNED_CONFIG": str(ROOT / "docker/cos-agent-a/openclaw.json"),
           "COS_RUNTIME_MODEL_ACCESS": "plan", "COS_PLAN_MODEL": "openai/gpt-6-astra", "COS_PLAN_AUTH_PROFILE": "openai:robert", "COS_METERED_ROUTES": R.GROK_REF,
           "COS_FAILOVER_ROUTES": "minimoi-gateway/minimoi-openai-strong", "COS_GATEWAY_KEY_CAPPED": "1"}
    got = json.loads(REAL_RUN(["node", "-e", R.COS_POLICY_JS], capture_output=True, text=True, env=env, check=True).stdout)
    assert got["primary"] == "openai/gpt-6-astra" and got["allow"] == ["openai/gpt-6-astra", R.GROK_REF, "minimoi-gateway/minimoi-openai-strong"]
    assert got["provider"]["apiKey"] == "${MINIMOI_MODEL_GATEWAY_KEY}"


# ── Pure checks ──

def test_only_env_names_a_field_never_a_value():
    before = {"services": {"s": {"image": "i", "environment": {"A": "secret-one"}}}, "networks": {}}
    after = copy.deepcopy(before)
    after["services"]["s"]["environment"].update(A="secret-two", B="new")
    with pytest.raises(AssertionError) as caught:
        R.only_env(before, after, {"s": {"B": "new"}})
    assert "/s/environment/A" in str(caught.value) and "secret" not in str(caught.value)
    R.only_env(before, after, {"s": {"A": "secret-two", "B": "new"}})
    after["services"]["s"]["image"] = "other"
    with pytest.raises(AssertionError, match="/s/image"):
        R.only_env(before, after, {"s": {"A": "secret-two", "B": "new"}})


def test_contract_differs_only_in_the_named_environment():
    saved = {"env": ["A=1", "B=2"], "host": {"OomKillDisable": None}, "mounts": []}
    live = {"env": ["A=1", "B=3", "C=4"], "host": {"OomKillDisable": False}, "mounts": []}
    R.check_contract(saved, live, {"B", "C"}, "x")
    with pytest.raises(AssertionError) as caught:
        R.check_contract(saved, live, {"C"}, "x")
    assert "/env" in str(caught.value) and "B=3" not in str(caught.value)


def test_cos_key_kinds():
    assert R.classify_cos_key({"MINIMOI_MODEL_GATEWAY_KEY": MASTER}, ["/run/cos-key"], MASTER) == "file"
    assert R.classify_cos_key({"MINIMOI_MODEL_GATEWAY_KEY": COS_KEY}, [], MASTER) == "env"
    assert R.classify_cos_key({"MINIMOI_MODEL_GATEWAY_KEY": MASTER}, [], MASTER) == "master"
    with pytest.raises(R.Refused, match="no budget"):
        R.check_existing_key({"max_budget": None, "models": ["a"]})
    with pytest.raises(R.Refused, match="lists no models"):
        R.check_existing_key({"max_budget": 30, "models": []})


def test_routed_policy_is_the_old_check_plus_only_the_named_provider():
    provider = {"baseUrl": "http://127.0.0.1:4000/v1", "apiKey": "${MC_MODEL_GATEWAY_KEY}", "api": "openai-completions", "models": []}
    want = {"provider": provider, "allow": ["openai/a", R.GROK_REF], "primary": "openai/a", "fallbacks": []}
    agent = {"model": {"primary": "openai/a", "fallbacks": []}, "modelPolicy": {"allow": want["allow"]}}
    live = {"models": {"providers": {"minimoi-gateway": provider}}, "auth": {"profiles": {"p": {"mode": "oauth"}}},
            "agents": {"defaults": agent, "entries": {"mc-chat": {**agent, "tools": {"deny": ["group:runtime"], "fs": {"workspaceOnly": True}}}, "mc-agent": {**agent, "tools": {"deny": []}}}}}
    R.check_routed_mc_policy(live, want, ["sk-held"])
    with pytest.raises(AssertionError, match="key value"):
        R.check_routed_mc_policy({**live, "note": "sk-held"}, want, ["sk-held"])
    extra = copy.deepcopy(live)
    extra["models"]["providers"]["other"] = {}
    with pytest.raises(AssertionError, match="not the planned one"):
        R.check_routed_mc_policy(extra, want)
    fallback = copy.deepcopy(live)
    fallback["agents"]["entries"]["mc-chat"]["model"] = {"primary": "openai/a", "fallbacks": [R.GROK_REF]}
    with pytest.raises(AssertionError, match="fallback"):
        R.check_routed_mc_policy(fallback, want)
    api = copy.deepcopy(live)
    api["auth"]["profiles"]["q"] = {"mode": "api_key"}
    with pytest.raises(AssertionError, match="API profile"):
        R.check_routed_mc_policy(api, want)


# ── The order, with every host step faked ──

class Boom(Exception):
    pass


class StepHost:
    def __init__(self, **fail):
        self.fail, self.calls, self.records = fail, [], {}

    def __getattr__(self, name):
        def step(*args):
            self.calls.append(name)
            if self.fail.get(name):
                raise Boom(name)
            return {"changed": "nothing live"} if name == "plan" else None
        return step

    @contextmanager
    def gate(self):
        self.calls.append("gate_open")
        yield "held"
        self.calls.append("gate_closed")

    def gate_alive(self, _):
        return True

    def record(self, name, detail):
        self.records[name] = detail


def test_dry_run_only_reads():
    host = StepHost()
    assert R.RouteSwitch(host, gs.guarded_swap).dry_run() == {"changed": "nothing live"}
    assert host.calls == ["check_baseline", "check_disk", "plan"]


def test_apply_order_keys_before_any_stop_runtime_before_catalogue():
    host = StepHost()
    assert R.RouteSwitch(host, gs.guarded_swap).apply() is gs.Outcome.APPLIED
    assert host.calls == ["check_baseline", "check_disk", "plan", "save_before", "ensure_keys", "stop_workers", "wait_idle", "gate_open", "write_override", "recreate", "write_catalogue",
                          "verify_new", "gate_closed", "start_workers", "verify_workers"]
    assert host.records["result"] == {"status": "routes_on_verified"}


def test_a_key_refusal_costs_no_downtime():
    host = StepHost(ensure_keys=True)
    with pytest.raises(Boom):
        R.RouteSwitch(host, gs.guarded_swap).apply()
    assert "stop_workers" not in host.calls


def test_a_failed_worker_check_after_apply_rolls_back():
    host = StepHost()
    calls = {"n": 0}

    def verify_workers():
        calls["n"] += 1
        host.calls.append("verify_workers")
        if calls["n"] == 1:
            raise Boom("worker")
    host.verify_workers = verify_workers
    with pytest.raises(Boom):
        R.RouteSwitch(host, gs.guarded_swap).apply()
    assert host.calls.count("restore_verified") == 1 and host.records["result"] == {"status": "rolled_back_verified"}


# ── A real host's handling, on a folder, with Docker faked ──

class Result:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


class FakeDocker:
    """Enough of Docker and the gateway for the host class: containers, compose by wrapper path, and a key store."""

    def __init__(self, root):
        self.root, self.log, self.fail_up = root, [], {}
        self.after_up = None
        self.minted = []
        self.up_counts = {}
        self.keys = {sha(COS_KEY.encode()): {"models": json.loads(subprocess_cos_models()), "max_budget": 30.0, "budget_duration": "30d", "spend": 1.5, "key_alias": "cos-agent-1", "blocked": None}}
        self.gateway_answer = {"missing": [], "no_provider_key": [], "in_a_fallback": [], "key_database": True}
        env_cos = {"COS_RUNTIME_MODEL_ACCESS": "plan", "COS_PLAN_MODEL": "openai/gpt-6-astra", "COS_PLAN_AUTH_PROFILE": "openai:robert", "COS_GATEWAY_KEY_CAPPED": "1", "MINIMOI_MODEL_GATEWAY_KEY": COS_KEY, "MINIMOI_ENV": "dev"}
        env_mc = {"MC_RUNTIME_MODEL_ACCESS": "plan", "MC_PLAN_MODEL": "openai/gpt-6-astra", "MC_PLAN_ROUTES": "openai/gpt-6-sol", "MC_PLAN_AUTH_PROFILE": "openai:owner", "MC_TOOLS_TOKEN": TOOLS, "MINIMOI_ENV": "dev"}
        env_relay = {"MC_RELAY_ROUTES": json.dumps(RELAY_START), "MC_RELAY_AGENT_DB": "/mc-state/agents/mc-agent/openclaw-agent.sqlite", "MC_RELAY_TOKEN": "relay-token-value"}
        self.services = {
            "compose": {"cos-agent-a": {"container_name": R.COS, "image": "cos:beta", "environment": dict(env_cos)},
                        "portal": {"container_name": "minimoi-portal", "image": "portal:beta", "environment": {"MINIMOI_ROUTES": "x"}}},
            "mc": {"mc-agent": {"container_name": R.MC, "image": "mc:beta", "environment": dict(env_mc)},
                   "mc-relay": {"container_name": R.RELAY, "image": "mc:beta", "environment": dict(env_relay)}}}
        state = root / "mc-state"
        mounts = {R.MC: [{"Destination": R.STATE_DEST, "Source": str(state), "Name": "mc-state"}],
                  R.RELAY: [{"Destination": "/mc-state", "Source": str(state), "Name": "mc-state"}], R.COS: []}
        catalogue_env = ["MINIMOI_MODEL_ROUTES_FILE=/catalogue/catalogue.json"]
        self.containers = {}
        for wrapper, services in self.services.items():
            for service, spec in services.items():
                self._make(spec["container_name"], spec, mounts.get(spec["container_name"], []))
        self.containers["minimoi-portal"]["Config"]["Labels"]["com.docker.compose.project"] = "minimoi-staging"
        self.containers["minimoi-portal"]["Config"]["Env"] += catalogue_env
        self.containers[R.RELAY]["Config"]["Entrypoint"] = ["tini", "-g", "--", "node", "/opt/minimoi/mc-agent/relay_v2.mjs"]
        for name, image in (("minimoi-cos-scheduler", "s"), (R.GATEWAY_CONTAINER, "g"), ("minimoi-guildhall-mc", "w"), ("minimoi-partnership-worker", "w")):
            self.containers[name] = {"Id": "c-" + name, "Image": "sha256:" + image, "Name": "/" + name, "Mounts": [], "State": {"Running": True}, "HostConfig": {},
                                     "Config": {"Image": image, "Env": [], "Labels": {}}}
        self.containers["minimoi-cos-scheduler"]["Config"]["Env"] += [*catalogue_env, "COS_BACKGROUND_JOBS=0"]
        self.containers[R.GATEWAY_CONTAINER]["Config"]["Env"] += ["LITELLM_MASTER_KEY=" + MASTER, "DATABASE_URL=x"]
        self.containers["minimoi-guildhall-mc"]["Config"]["Env"] += ["ROOMS_RECORDS_PROFILE=dev"]

    def _make(self, name, spec, mounts):
        self.containers[name] = {"Id": "c1-" + name, "Image": "sha256:" + spec["image"], "Name": "/" + name, "Mounts": mounts, "State": {"Running": True}, "HostConfig": {"OomKillDisable": None},
                                 "Config": {"Image": spec["image"], "Env": ["PATH=/bin", *[f"{k}={v}" for k, v in spec["environment"].items()]], "Labels": {}}}

    def render(self, wrapper, extra=()):
        doc = {"services": copy.deepcopy(self.services[wrapper]), "networks": {}}
        text = (self.root / f"bin/{'compose' if wrapper == 'compose' else 'mc'}.sh").read_text()
        files = [*re.findall(r"-f (\S*routes\.yml)", text), *extra]
        for path in files:
            for service, spec in json.loads(Path(path).read_text())["services"].items():
                doc["services"][service]["environment"].update(spec["environment"])
        return doc

    def _compose(self, args):
        wrapper = "compose" if args[0].endswith("compose.sh") else "mc"
        rest, extra = list(args[1:]), []
        while rest[:1] == ["-f"]:
            extra.append(rest[1])
            rest = rest[2:]
        if rest[0] == "config":
            return Result(json.dumps(self.render(wrapper, extra)))
        if rest[0] == "up":
            service = rest[-1]
            self.log.append(("up", service))
            self.up_counts[service] = self.up_counts.get(service, 0) + 1
            if self.up_counts[service] in self.fail_up.get(service, ()):
                return Result("", 1, "compose failed")
            spec = self.render(wrapper)["services"][service]
            name = spec["container_name"]
            image_env = ["PATH=/bin"]
            live = self.containers[name]
            live["Config"]["Env"] = [*image_env, *[f"{k}={v}" for k, v in spec["environment"].items()]]
            live["State"]["Running"] = True
            live["Id"] += "+"
            self._runtime(name, spec["environment"])
            if self.after_up:
                self.after_up(name, live)
            return Result("")
        raise AssertionError(args)

    def _runtime(self, name, env):
        """What the real start scripts do from this environment."""
        if name == R.MC:
            path = self.root / "mc-state/openclaw.json"
            config = json.loads(path.read_text())
            if env.get("MC_METERED_ROUTES"):
                config["models"] = {"providers": {"minimoi-gateway": mc_want()["provider"]}}
                for agent in R._agents(config):
                    agent["modelPolicy"]["allow"] = mc_want()["allow"]
            path.write_text(json.dumps(config))

    def __call__(self, args, capture_output=True, text=True, timeout=None, input=None, env=None, check=False, **_):
        args = [str(a) for a in args]
        if args[0] == "bash":
            return REAL_RUN(args, capture_output=True, text=True, check=check)
        if args[0].endswith(".sh"):
            self.log.append(("compose", args[1:3]))
            return self._compose(args)
        assert args[0] == "docker", args
        if args[1] == "image":
            return Result(json.dumps([{"Id": "sha256:" + args[3], "Config": {}}]))
        if args[1] in ("stop", "start"):
            self.log.append((args[1], args[2:]))
            for name in args[2:]:
                if name in self.containers:
                    self.containers[name]["State"]["Running"] = args[1] == "start"
            return Result("")
        if args[1] == "run":
            self.log.append(("sealed",))
            return self._sealed(args, input)
        if args[1] == "exec":
            return self._exec(args[2:], input, env)
        raise AssertionError(args)

    def _sealed(self, args, input):
        joined = " ".join(args)
        if "relay_routes" in joined:
            return self._relay_routes(input)
        if "COS_FAILOVER_ROUTES" in joined and "plan-access" in joined:
            return Result(json.dumps(cos_want()) + "\n")
        return Result(json.dumps(mc_want()) + "\n")

    def _relay_routes(self, text):
        path = self.root / "sealed.json"
        path.write_text(text)
        routes = relay_routes(load_catalogue(path))
        path.unlink()
        return Result(json.dumps(routes, separators=(",", ":"), sort_keys=True))

    def _exec(self, args, input, env):
        names = []
        while args[0] in ("-i", "-e"):
            if args[0] == "-e":
                names.append(args[1])
                args = args[2:]
            else:
                args = args[1:]
        name, *cmd = args
        if name == R.GATEWAY_CONTAINER:
            return self._gateway(input or "", {n: (env or {}).get(n) for n in names})
        if cmd[0] == "grep":
            return Result("")
        if cmd[0] == "test":
            return Result("", 0)
        if cmd[0] == "sha256sum":
            data = (self.root / "config/model-routes/catalogue.json").read_bytes()
            return Result(f"{sha(data)}  {cmd[1]}\n")
        if cmd[0] == "cat":
            return Result(json.dumps(cos_runtime(self.containers[R.COS])))
        if cmd[0] == "python":
            return Result("0\n")
        if cmd[0] == "node":
            return Result("")
        if cmd[0] == "head":
            return Result(COS_KEY + "\n")
        raise AssertionError(args)

    def _gateway(self, code, env):
        if "/key/generate" in code:
            self.log.append(("key_generate", re.search(r'"models": (\[.*?\])', code).group(1), env["ROUTES_CAP"]))
            key = f"sk-minted-gateway-key-{len(self.minted):04d}-xyz"
            self.minted.append(key)
            self.keys[sha(key.encode())] = {"models": json.loads(re.search(r'"models": (\[.*?\])', code).group(1)), "max_budget": float(env["ROUTES_CAP"]), "budget_duration": "30d", "spend": 0, "key_alias": "mc-routes-x", "blocked": None}
            return Result(key + "\nab12\n")
        if "/key/info" in code:
            return Result(json.dumps(self.keys[sha(env["ROUTES_KEY"].encode())]) + "\n")
        if "/key/update" in code:
            self.log.append(("key_update", json.loads(env["ROUTES_MODELS"])))
            self.keys[sha(env["ROUTES_KEY"].encode())]["models"] = json.loads(env["ROUTES_MODELS"])
            return Result("updated\n")
        if "routes" in code or "ROUTES_WANT" in code:
            return Result(json.dumps(self.gateway_answer) + "\n")
        raise AssertionError(code)


def subprocess_cos_models():
    return json.dumps(R.cos_base_models(ROOT / "scripts/staging/mc_keys.sh"))


def mc_want():
    return {"provider": {"baseUrl": "http://127.0.0.1:4000/v1", "apiKey": "${MC_MODEL_GATEWAY_KEY}", "api": "openai-completions", "models": []},
            "allow": ["openai/gpt-6-astra", "openai/gpt-6-sol", R.GROK_REF, "minimoi-gateway/minimoi-openai-strong"], "primary": "openai/gpt-6-astra", "fallbacks": []}


def cos_want():
    return {"provider": {"baseUrl": "http://model-gateway:4000/v1", "apiKey": "${MINIMOI_MODEL_GATEWAY_KEY}", "api": "openai-completions", "models": [{"id": R.GROK_MODEL}]},
            "allow": ["openai/gpt-6-astra", R.GROK_REF], "primary": "openai/gpt-6-astra", "fallbacks": []}


def cos_runtime(_):
    agent = {"model": {"primary": "openai/gpt-6-astra", "fallbacks": []}, "modelPolicy": {"allow": cos_want()["allow"]}}
    return {"models": {"providers": {"minimoi-gateway": cos_want()["provider"]}}, "agents": {"defaults": agent, "entries": {"cos-agent-a": agent}}}


def mc_runtime():
    agent = {"model": {"primary": "openai/gpt-6-astra", "fallbacks": []}, "modelPolicy": {"allow": ["openai/gpt-6-astra", "openai/gpt-6-sol"]}}
    return {"meta": {"lastTouchedAt": "x"}, "auth": {"profiles": {"openai:owner": {"provider": "openai", "mode": "oauth"}}},
            "agents": {"defaults": agent, "entries": {"mc-chat": {**agent, "tools": {"deny": ["group:runtime"], "fs": {"workspaceOnly": True}}}, "mc-agent": {**agent, "tools": {"deny": []}}}}}


class FakeOp:
    def __init__(self, docker):
        self.docker = docker

    def inspect(self, name):
        return copy.deepcopy(self.docker.containers[name])

    def contract(self, name):
        c = self.docker.containers[name]
        return {"env": sorted(c["Config"]["Env"]), "host": c["HostConfig"], "mounts": c["Mounts"]}

    def snapshot(self):
        return {n: {"id": c["Id"], "image": c["Image"]} for n, c in self.docker.containers.items()}

    def idle(self):
        return True

    @contextmanager
    def admission_gate(self):
        self.docker.log.append(("gate", "open"))
        yield "gate"
        self.docker.log.append(("gate", "closed"))

    def gate_alive(self, _):
        return True

    def wait_healthy(self, name, gate, limit):
        assert self.docker.containers[name]["State"]["Running"], name + " is not running"


class Rig:
    def __init__(self, root, monkeypatch, providers=("openai",)):
        self.root = root
        (root / "operators" / R.OPERATOR_NAME).mkdir(parents=True)
        for rel, text in (("bin/compose.sh", 'exec docker compose -p minimoi-staging -f /r/x.yml "$@"\n'), ("bin/mc.sh", 'exec docker compose -p minimoi-mc -f /r/y.yml "$@"\n'),
                          ("config/model-routes/catalogue.json", json.dumps(dev_catalogue())), ("mc-state/openclaw.json", json.dumps(mc_runtime()))):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.docker = FakeDocker(root)
        monkeypatch.setattr(subprocess, "run", self.docker)
        monkeypatch.setattr(R.time, "sleep", lambda *_: None)
        monkeypatch.setattr(R.shutil, "disk_usage", lambda _: type("U", (), {"free": 10 * 1024 ** 3}))
        monkeypatch.setattr(R.os, "chown", lambda *a: None)
        self.host = self.fresh(providers)
        self.OP = self.host.OP

    def fresh(self, providers=("openai",)):
        return R.RealHost(root=self.root, op_module=FakeOp(self.docker), providers=providers)

    def switch(self, host=None):
        return R.RouteSwitch(host or self.host, gs.guarded_swap)

    def live_tree(self):
        """Everything outside the operator's own folder, and every container and key."""
        files = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file() and self.OP not in p.parents}
        return files, json.dumps(self.docker.containers, sort_keys=True), json.dumps(self.docker.keys, sort_keys=True)

    def written_by_operator(self):
        return "\n".join(p.read_text() for p in self.OP.rglob("*") if p.is_file() and p.name in ("journal.jsonl", "plan.json", "outcome.json", "result.json", "recovery-required.json", "status.json"))


@pytest.fixture
def rig(tmp_path, monkeypatch):
    return Rig(tmp_path, monkeypatch)


def test_dry_run_changes_nothing_and_prints_the_plan(rig, capsys):
    before = rig.live_tree()
    plan = rig.switch().dry_run()
    assert rig.live_tree() == before
    assert not [e for e in rig.docker.log if e[0] in ("up", "stop", "start", "key_generate", "key_update", "gate")]
    assert not rig.docker.minted and not (rig.OP / "keys").exists() and not (rig.OP / "compose.routes.yml").exists()
    assert not list(rig.OP.glob("check.*"))
    out = capsys.readouterr().out
    assert "left OUT" in out and "--failover openai,anthropic is possible later" in out
    assert "CATALOGUE ~ route plan-openai-strong: + failover: api-openai-strong" in out and "DIFF " in out
    assert "KEY MC: new, $20 per 30d" in out and "KEY CoS: own key (env). Budget $30.0 per 30d" in out and "unchanged" in out
    assert "To revoke it:" in out
    assert plan["keys"]["cos"]["add"] == sorted(["minimoi-openai-strong", "minimoi-openai-mid", "minimoi-openai-low", "minimoi-xai-low"])


def test_apply_on_the_happy_path(rig, capsys):
    assert rig.switch().apply() is gs.Outcome.APPLIED
    live_mc = R.env_dict(rig.docker.containers[R.MC]["Config"]["Env"])
    assert live_mc["MC_METERED_ROUTES"] == R.GROK_REF
    assert live_mc["MC_FAILOVER_ROUTES"] == "minimoi-gateway/minimoi-openai-strong,minimoi-gateway/minimoi-openai-mid,minimoi-gateway/minimoi-openai-low"
    assert live_mc["MC_MODEL_GATEWAY_KEY"] == rig.docker.minted[0]
    relay = R.env_dict(rig.docker.containers[R.RELAY]["Config"]["Env"])
    assert relay["MC_RELAY_TOOL_STARTS"] == "/mc-state/mc-tool-starts" and relay["MC_RELAY_CHAT_DB"].startswith("/mc-state/agents/mc-chat/")
    assert "api-xai-low" in json.loads(relay["MC_RELAY_ROUTES"])
    # One key for MC at $20, failover tiers and Grok, no Claude tier.
    assert len(rig.docker.minted) == 1
    made = rig.docker.keys[sha(rig.docker.minted[0].encode())]
    assert made["max_budget"] == 20 and made["budget_duration"] == "30d" and sorted(made["models"]) == ["minimoi-openai-low", "minimoi-openai-mid", "minimoi-openai-strong", "minimoi-xai-low"]
    # CoS's own key extended, its budget left alone.
    cos = rig.docker.keys[sha(COS_KEY.encode())]
    assert cos["max_budget"] == 30.0 and "minimoi-xai-low" in cos["models"] and "minimoi-cos-web-search" in cos["models"]
    # Only the three recreated, workers stopped first and started after, gate held.
    ups = [e for e in rig.docker.log if e[0] == "up"]
    assert ups == [("up", "cos-agent-a"), ("up", "mc-relay"), ("up", "mc-agent")]
    order = [e[0] for e in rig.docker.log if e[0] in ("stop", "start", "gate", "up")]
    assert order.index("stop") < order.index("gate") < order.index("up") and order[-1] == "start"
    assert ("start", ["minimoi-guildhall-mc", "minimoi-partnership-worker"]) in rig.docker.log
    assert json.loads((rig.root / "config/model-routes/catalogue.json").read_text())["roles"]["mc-conversation"]["route"] == A
    assert (rig.OP / "result.json").exists()
    for wrapper in ("compose.sh", "mc.sh"):
        assert "routes.yml" in (rig.root / "bin" / wrapper).read_text()
    assert stat.S_IMODE((rig.OP / "compose.routes.yml").stat().st_mode) == 0o600 == stat.S_IMODE((rig.OP / "mc.routes.yml").stat().st_mode)
    assert stat.S_IMODE((rig.OP / "keys/mc-gateway.key").stat().st_mode) == 0o600


def test_a_key_value_never_reaches_stdout_the_journal_or_the_plan(rig, capsys):
    rig.switch().apply()
    captured = capsys.readouterr()
    text = captured.out + captured.err + rig.written_by_operator()
    assert '"event": "recreated"' in text and '"statement"' in text        # the journal and the plan are really in what was scanned
    for secret in (rig.docker.minted[0], COS_KEY, MASTER, TOOLS):
        assert secret not in text, "a value leaked"
    assert "<MC's capped key, from a 0600 file>" in text
    assert (rig.OP / "keys/mc-gateway.key").read_text().strip() == rig.docker.minted[0]


def test_a_failed_command_log_is_scrubbed(rig, monkeypatch):
    host = rig.host
    host.secrets.add("sk-held-value-0000000000000")
    monkeypatch.setattr(subprocess, "run", lambda args, **_: Result("sk-held-value-0000000000000 in the output", 1, "and sk-held-value-0000000000000 here"))
    with pytest.raises(RuntimeError) as caught:
        host._run("docker", "x")
    assert "sk-held" not in str(caught.value)
    log = (rig.OP / "last-command.private.log")
    assert "sk-held" not in log.read_text() and stat.S_IMODE(log.stat().st_mode) == 0o600


def test_a_failed_verify_restores_everything(rig, capsys):
    before = {p: (rig.root / p).read_bytes() for p in ("bin/compose.sh", "bin/mc.sh", "config/model-routes/catalogue.json")}
    runtime = json.loads((rig.root / "mc-state/openclaw.json").read_text())
    models = list(rig.docker.keys[sha(COS_KEY.encode())]["models"])

    def stray(name, live):                          # the MC container came up with a setting nobody named
        if name == R.MC and any(e.startswith("MC_METERED_ROUTES=") for e in live["Config"]["Env"]):
            live["Config"]["Env"].append("STRAY=1")
    rig.docker.after_up = stray
    with pytest.raises(AssertionError) as caught:
        rig.switch().apply()
    assert "STRAY" in str(caught.value) and "=1" not in str(caught.value)
    assert {p: (rig.root / p).read_bytes() for p in before} == before
    assert json.loads((rig.root / "mc-state/openclaw.json").read_text()) == runtime
    assert not (rig.OP / "compose.routes.yml").exists() and not (rig.OP / "mc.routes.yml").exists()
    assert "MC_METERED_ROUTES" not in " ".join(rig.docker.containers[R.MC]["Config"]["Env"])
    assert rig.docker.keys[sha(COS_KEY.encode())]["models"] == models
    assert json.loads((rig.OP / "outcome.json").read_text())["outcome"] == "restored"
    assert not (rig.OP / "recovery-required.json").exists()
    assert rig.docker.log[-1][0] == "start" or ("start", ["minimoi-guildhall-mc", "minimoi-partnership-worker"]) in rig.docker.log


def test_a_failed_restore_leaves_workers_stopped_with_recovery_required(rig, capsys):
    rig.docker.fail_up = {"mc-agent": {1}, "cos-agent-a": {2}}   # the swap fails at MC; the restore fails at CoS
    with pytest.raises(RuntimeError):
        rig.switch().apply()
    assert (rig.OP / "recovery-required.json").exists()
    assert not [e for e in rig.docker.log if e[0] == "start"]
    assert not rig.docker.containers["minimoi-guildhall-mc"]["State"]["Running"]
    assert json.loads((rig.OP / "outcome.json").read_text())["outcome"] == "recovery_required"
    with pytest.raises(AssertionError, match="needs recovery"):
        rig.fresh().check_baseline()


def test_rollback_restores_and_says_how_to_revoke_and_a_reapply_reuses_the_key(rig, capsys):
    before = {p: (rig.root / p).read_bytes() for p in ("bin/compose.sh", "bin/mc.sh", "config/model-routes/catalogue.json")}
    models = list(rig.docker.keys[sha(COS_KEY.encode())]["models"])
    rig.switch().apply()
    capsys.readouterr()
    assert rig.switch(rig.fresh()).rollback() is gs.Outcome.APPLIED      # the rollback itself is what guarded_swap applied, as in beta_dev_deploy
    out = capsys.readouterr().out
    assert "To revoke it:" in out and "key/delete" in out and rig.docker.minted[0] not in out
    assert {p: (rig.root / p).read_bytes() for p in before} == before
    assert rig.docker.keys[sha(COS_KEY.encode())]["models"] == models
    assert (rig.OP / "keys/mc-gateway.key").exists()
    assert json.loads((rig.OP / "result.json").read_text())["status"] == "rolled_back_verified"
    rig.switch(rig.fresh()).apply()
    assert len(rig.docker.minted) == 1, "a re-apply must not make a second key"


def test_a_cos_on_the_master_key_gets_a_new_key_at_the_cap(rig, capsys):
    rig.docker.services["compose"]["cos-agent-a"]["environment"]["MINIMOI_MODEL_GATEWAY_KEY"] = MASTER
    rig.docker.services["compose"]["cos-agent-a"]["environment"].pop("COS_GATEWAY_KEY_CAPPED")
    live = rig.docker.containers[R.COS]["Config"]
    live["Env"] = [e for e in live["Env"] if not e.startswith(("MINIMOI_MODEL_GATEWAY_KEY=", "COS_GATEWAY_KEY_CAPPED="))] + ["MINIMOI_MODEL_GATEWAY_KEY=" + MASTER]
    rig.switch().apply()
    assert len(rig.docker.minted) == 2
    cos_key = (rig.OP / "keys/cos-gateway.key").read_text().strip()
    info = rig.docker.keys[sha(cos_key.encode())]
    assert info["max_budget"] == 20 and "minimoi-cos-web-search" in info["models"] and "minimoi-xai-low" in info["models"]
    env = R.env_dict(rig.docker.containers[R.COS]["Config"]["Env"])
    assert env["MINIMOI_MODEL_GATEWAY_KEY"] == cos_key and env["COS_GATEWAY_KEY_CAPPED"] == "1"
    captured = capsys.readouterr()
    assert MASTER not in captured.out and cos_key not in captured.out


def test_an_uncapped_existing_cos_key_is_refused(rig):
    rig.docker.keys[sha(COS_KEY.encode())]["max_budget"] = None
    before = rig.live_tree()
    with pytest.raises(R.Refused, match="no budget"):
        rig.switch().dry_run()
    assert rig.live_tree() == before


# ── Refusals ──

def test_refuses_production_by_path():
    with pytest.raises(D.Refused):
        R.RealHost(root="/opt/minimoi", op_module=object())
    with pytest.raises(RuntimeError, match="production path refused"):     # another test module may have loaded its own copy of the class
        payload.P.check_destination("/opt/minimoi/operators/x")


@pytest.mark.parametrize("value", ["MINIMOI_ROLE=production", "MINIMOI_ENV=production"])
def test_refuses_a_host_that_says_production(rig, value):
    rig.docker.containers["minimoi-portal"]["Config"]["Env"].append(value)
    with pytest.raises(R.Refused, match="production"):
        rig.switch().dry_run()


def test_refuses_a_wrapper_that_names_production(rig):
    (rig.root / "bin/mc.sh").write_text('exec docker compose -f /opt/minimoi/x.yml "$@"\n')
    with pytest.raises(D.Refused, match="production path"):
        rig.switch().dry_run()


def test_refuses_a_portal_of_another_project(rig):
    rig.docker.containers["minimoi-portal"]["Config"]["Labels"]["com.docker.compose.project"] = "minimoi"
    with pytest.raises(AssertionError, match="not Dev's"):
        rig.switch().dry_run()


def test_refuses_a_second_apply(rig):
    rig.switch().apply()
    with pytest.raises(AssertionError, match="already applied"):
        rig.switch(rig.fresh()).apply()


def test_refuses_drift_between_compose_and_what_runs(rig):
    rig.docker.services["mc"]["mc-agent"]["environment"]["MC_PLAN_ROUTES"] = "openai/other"
    with pytest.raises(R.Refused) as caught:
        rig.switch().dry_run()
    assert "MC_PLAN_ROUTES" in str(caught.value) and "openai/other" not in str(caught.value)


def test_refuses_a_relay_without_mcs_state(rig):
    rig.docker.containers[R.RELAY]["Mounts"] = []
    with pytest.raises(R.Refused, match="does not mount MC's state"):
        rig.switch().dry_run()


def test_refuses_mc_in_gateway_mode(rig):
    env = rig.docker.containers[R.MC]["Config"]["Env"]
    env[:] = [e for e in env if not e.startswith("MC_RUNTIME_MODEL_ACCESS=")] + ["MC_RUNTIME_MODEL_ACCESS=gateway"]
    rig.docker.services["mc"]["mc-agent"]["environment"]["MC_RUNTIME_MODEL_ACCESS"] = "gateway"
    with pytest.raises(R.Refused, match="plan mode"):
        rig.switch().dry_run()


@pytest.mark.parametrize("field,value,words", [("missing", ["minimoi-xai-low"], "no tier"), ("no_provider_key", ["GATEWAY_XAI_API_KEY"], "no provider key"),
                                               ("in_a_fallback", ["minimoi-xai-low"], "fallback chain"), ("key_database", False, "no key database")])
def test_refuses_a_gateway_that_cannot_serve_this(rig, field, value, words):
    rig.docker.gateway_answer[field] = value
    with pytest.raises(R.Refused, match=words):
        rig.switch().dry_run()


def test_rollback_refuses_a_wrapper_moved_since_apply(rig):
    rig.switch().apply()
    (rig.root / "bin/mc.sh").write_text('exec docker compose -f other.yml "$@"\n')
    with pytest.raises(R.Refused, match="changed since"):
        rig.switch(rig.fresh()).rollback()


# ── Sending it ──

def test_the_start_script_is_detached_and_defaults_stay_out_of_claude():
    script = payload.start_script("apply", R.DEFAULT_FAILOVER)
    assert "nohup setsid" in script and "--failover openai >" in script and payload.HOST_DIR in script
    with pytest.raises(R.Refused):
        payload.start_script("apply", "xai")


def test_the_transfer_uses_this_operators_applied_mark():
    scripts = payload.scripts_for(b"tar", sha(b"tar"))
    assert "compose.routes.yml" in scripts[-1] and "compose.beta.yml" not in scripts[-1]


def test_the_operator_ships_every_file_it_imports(tmp_path):
    names = {Path(p).name for p in payload.OPERATOR_FILES}
    assert {"beta_dev_routes.py", "beta_dev_deploy.py", "guarded_swap.py", "mc_keys.sh"} == names
    assert all((ROOT / p).is_file() for p in payload.OPERATOR_FILES)
