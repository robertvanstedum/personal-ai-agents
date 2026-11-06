#!/usr/bin/env python3
"""Dev only: switches on MiniMoi's selectable, capped Grok API route and plan-to-API failover (docs/runbooks/model_routes_plan.md).

``dry-run`` changes nothing and prints the exact plan. ``apply`` and ``rollback`` run detached, under the deploy lock, through
guarded_swap: workers resume only after a verified result. Same running images, one override this operator owns, layered last.

Robert's decisions, 6 Nov 2026: Grok is pay-per-use, selectable, never a default, and nothing falls back onto it. Failover targets
are chosen by provider; ``openai`` is the default because the "has a tool started?" evidence on Claude routes is unproved.
Caps: $20 per 30 days on any key made here. An existing key's budget is reported and never changed.

No test turn and no model call happens here. Key management calls reach only the gateway's own /key/* endpoints.
Never touches production, default route A, *_ENV, ROOMS_RECORDS_PROFILE, authority files, launch policy or gates."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import difflib
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import beta_dev_deploy as D                         # noqa: E402

OPERATOR_NAME = "beta-dev-routes-20261106"
BETA_OPERATOR = D.OPERATOR_NAME
CAP_DOLLARS = 20
BUDGET_DURATION = "30d"
GATEWAY_CONTAINER = "minimoi-model-gateway"
GATEWAY = "minimoi-gateway"
GROK_ROUTE, GROK_MODEL = "api-xai-low", "minimoi-xai-low"
GROK_REF = f"{GATEWAY}/{GROK_MODEL}"
GROK_OFFERED = {"mc": "available", "cos": "available"}
PROVIDERS = ("openai", "anthropic")
DEFAULT_FAILOVER = "openai"
COS, RELAY, MC = "minimoi-cos-agent-a", "minimoi-mc-relay", "minimoi-mc-agent"
ORDER = ((COS, D.AGENT_WAIT, "up to 11 minutes (gateway owner lease); CoS refuses messages meanwhile"),
         (RELAY, D.DEFAULT_WAIT, "under 1 minute, inside MC's window"),
         (MC, D.MC_WAIT, "6 to 12 minutes (owner lease plus a 3-minute healthcheck)"))
CATALOGUE_READERS = ("minimoi-portal", "minimoi-cos-scheduler")
KEY = re.compile(r"^sk-[A-Za-z0-9_-]{16,200}$")
PLACEHOLDER_KEY = "sk-routes-dry-run-placeholder-not-a-key"
STATE_DEST = "/home/node/.openclaw"
COS_KEY_MOUNT = "/run/cos-key"
FORBIDDEN_MC_ENV = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "MC_ANTHROPIC_API_KEY")


def _api(route_id, tier, model, label, effort):
    return {"route_id": route_id, "tier": tier, "provider": GATEWAY, "model": model, "label": label, "reasoning_effort": effort,
            "billing_mode": "metered_api", "runtime": "openclaw", "auth_profile_ref": None, "capabilities": ["chat", "tools"], "offered": {}}


# A host's catalogue may predate these. Same as core/model_routes/catalogue.json: tests hold them equal.
API_ROUTES = {r["route_id"]: r for r in (
    _api("api-openai-strong", "strong", "minimoi-openai-strong", "GPT-6 Astra (API)", "high"),
    _api("api-openai-mid", "mid", "minimoi-openai-mid", "GPT-6.1 Sol (API)", "medium"),
    _api("api-openai-low", "low", "minimoi-openai-low", "GPT-6 Luna (API)", "low"),
    _api("api-anthropic-strong", "strong", "minimoi-anthropic-strong", "Claude Opus 5.5 (API)", "high"),
    _api("api-anthropic-mid", "mid", "minimoi-anthropic-mid", "Claude Sonnet 5.5 (API)", "medium"),
    _api(GROK_ROUTE, "low", GROK_MODEL, "Grok 4.3 (pay per use)", "low"),
)}


class Refused(D.Refused):
    """Stopped before anything live changed."""


# ── Pure planning, tested without a host ──

def parse_failover(text: str) -> tuple:
    names = [p.strip() for p in str(text).split(",") if p.strip()]
    if not names or len(set(names)) != len(names):
        raise Refused("--failover names one or more providers, once each")
    for name in names:
        if name not in PROVIDERS:
            raise Refused(f"--failover cannot name {name}: only {', '.join(PROVIDERS)}. Nothing falls back onto Grok.")
    return tuple(p for p in PROVIDERS if p in names)


def failover_statement(providers) -> str:
    said = f"Failover targets: {', '.join(providers)}."
    if "anthropic" in providers:
        return said + " Claude routes' failover is ON: the 'has a tool started?' evidence on Claude routes is unproved (Grok's review)."
    return said + (" Claude routes' failover is left OUT: the 'has a tool started?' evidence on Claude routes is unproved (Grok's review)."
                   " --failover openai,anthropic is possible later.")


def _is_target_of(route_id: str, providers) -> bool:
    return route_id == GROK_ROUTE or any(route_id.startswith(f"api-{p}-") for p in providers)


def plan_catalogue(old: dict, providers) -> tuple:
    """(new catalogue, semantic diff lines). Everything else in the host's file stays as it is, including its revision."""
    new = copy.deepcopy(old)
    routes, lines = new["routes"], []
    for route_id, template in API_ROUTES.items():
        have = next((r for r in routes if r["route_id"] == route_id), None)
        if have is None:
            if _is_target_of(route_id, providers):
                routes.append(copy.deepcopy(template))
                lines.append(f"+ route {route_id}: {GATEWAY}/{template['model']}, metered_api, offered {{}}")
        elif _is_target_of(route_id, providers) and (have.get("model") != template["model"] or have.get("billing_mode") != "metered_api" or have.get("provider") != GATEWAY):
            raise Refused(f"the host's {route_id} is not {GATEWAY}/{template['model']} on metered_api; nothing changed")
    by_id = {r["route_id"]: r for r in routes}
    for route in routes:
        have = route.get("failover")
        if route.get("billing_mode") != "subscription":
            if have is not None:
                raise Refused(f"the host's {route['route_id']} is not a plan route but declares a failover; nothing changed")
            continue
        target = f"api-{route['provider']}-{route['tier']}"
        if have is not None and (route["provider"] not in providers or have != target):
            raise Refused(f"the host's {route['route_id']} already fails over to {have}, which this plan does not choose; decide that first. Nothing changed.")
        if have is None and route["provider"] in providers:
            if target in by_id:
                route["failover"] = target
                lines.append(f"~ route {route['route_id']}: + failover: {target}")
            else:
                lines.append(f"= route {route['route_id']}: no failover line, {target} does not exist")
    grok = by_id[GROK_ROUTE]
    if grok["offered"] != GROK_OFFERED:
        lines.append(f"~ route {GROK_ROUTE}: offered {json.dumps(grok['offered'], sort_keys=True)} -> {json.dumps(GROK_OFFERED, sort_keys=True)}")
        grok["offered"] = dict(GROK_OFFERED)
    check_new_catalogue(old, new, providers)
    return new, lines


def check_new_catalogue(old: dict, new: dict, providers) -> None:
    """Default routes unchanged; Grok a choice only; every old field where it was, but the stated additions."""
    D.check_catalogue(old)
    D.check_catalogue(new)
    assert new["roles"] == old["roles"], "a role moved"
    assert {k: v for k, v in new.items() if k != "routes"} == {k: v for k, v in old.items() if k != "routes"}, "changed beyond its routes"
    if any(role["route"] == GROK_ROUTE for role in new["roles"].values()):
        raise Refused("a role reads Grok: Grok is never a default")
    for route in new["routes"]:
        if route.get("failover") == GROK_ROUTE or "xai" in str(route.get("failover") or ""):
            raise Refused("a failover names Grok: nothing falls back onto it")
    was = {r["route_id"]: r for r in old["routes"]}
    assert [r["route_id"] for r in new["routes"]][:len(old["routes"])] == [r["route_id"] for r in old["routes"]], "routes reordered"
    for route in new["routes"]:
        rid = route["route_id"]
        if rid not in was:
            assert _is_target_of(rid, providers), "route added beyond the plan: " + rid
            continue
        drop = {"failover"} if rid != GROK_ROUTE else {"failover", "offered"}
        assert {k: v for k, v in route.items() if k not in drop} == {k: v for k, v in was[rid].items() if k not in drop}, "route changed: " + rid
        if rid != GROK_ROUTE and route.get("failover") != was[rid].get("failover"):
            assert route["provider"] in providers, "failover beyond the plan: " + rid


def catalogue_text(catalogue: dict) -> str:
    return json.dumps(catalogue, indent=1) + "\n"


def catalogue_diff(old: dict, new: dict) -> list:
    """The exact change, line by line, between the two as JSON. The file on the host is rewritten in this layout."""
    return [line for line in difflib.unified_diff(catalogue_text(old).splitlines(), catalogue_text(new).splitlines(), "catalogue.before", "catalogue.after", lineterm="", n=0)]


def _ref(route: dict) -> str:
    return f"{route['provider']}/{route['model']}"


def failover_refs(catalogue: dict, agent: str, provider=None) -> list:
    """The API targets declared for this agent's plan routes, in catalogue order. Grok is never one of them."""
    by_id = {r["route_id"]: r for r in catalogue["routes"]}
    out = []
    for route in catalogue["routes"]:
        if route.get("failover") and agent in route["offered"] and (provider is None or route["provider"] == provider):
            ref = _ref(by_id[route["failover"]])
            if ref not in out:
                out.append(ref)
    return out


def route_env(catalogue: dict) -> dict:
    """{container: {name: value}}, the settings without the key. CoS's runtime holds OpenAI targets only (plan-access.cjs)."""
    return {COS: {"COS_METERED_ROUTES": GROK_REF, "COS_FAILOVER_ROUTES": ",".join(failover_refs(catalogue, "cos", "openai"))},
            MC: {"MC_METERED_ROUTES": GROK_REF, "MC_FAILOVER_ROUTES": ",".join(failover_refs(catalogue, "mc"))}}


def key_models(env_value: str) -> list:
    """The gateway tiers a key must allow: the failover targets and Grok."""
    refs = [r for r in env_value.split(",") if r]
    return sorted({r.split("/")[1] for r in refs} | {GROK_MODEL})


def check_relay_routes(old_raw, new_raw: str, catalogue: dict, providers) -> dict:
    """The relay keeps every route it knows and gains only the declared API routes."""
    old, new = json.loads(old_raw or "{}"), json.loads(new_raw)
    for route_id, ref in old.items():
        assert new.get(route_id) == ref, "the relay would lose or move a route: " + route_id
    for route_id in sorted(set(new) - set(old)):
        assert _is_target_of(route_id, providers), "the relay would gain a route beyond the plan: " + route_id
    assert new.get(GROK_ROUTE) == GROK_REF, "the relay would not know Grok"
    for ref in failover_refs(catalogue, "mc"):
        assert ref in new.values(), "the relay would not know a failover target: " + ref
    return new


def classify_cos_key(env: dict, mounts, master: str) -> str:
    """How CoS holds its gateway key (docker/cos-agent-a/apply-config.sh): a readable key file wins, else the environment's."""
    file_path = env.get("COS_GATEWAY_KEY_FILE") or COS_KEY_MOUNT + "/key"
    if any(file_path == m or file_path.startswith(m.rstrip("/") + "/") for m in mounts):
        return "file"
    value = env.get("MINIMOI_MODEL_GATEWAY_KEY", "")
    if value and value != master and KEY.match(value):
        return "env"
    return "master"


def check_existing_key(info: dict) -> None:
    """An existing key is extended only when it is capped and lists its models. Nothing here narrows or changes a budget."""
    if info.get("max_budget") is None:
        raise Refused("CoS's existing key has no budget: an uncapped key cannot serve a pay-per-use route. Nothing changed.")
    if not info.get("models"):
        raise Refused("CoS's existing key lists no models (it may call every model). Nothing changed; review it first.")
    if info.get("blocked"):
        raise Refused("CoS's existing key is blocked. Nothing changed.")


def check_new_key(info: dict, wanted) -> None:
    assert info.get("max_budget") == CAP_DOLLARS, "a key this operator holds is not capped at the agreed amount"
    assert info.get("budget_duration") == BUDGET_DURATION, "a key this operator holds has another budget period"
    assert sorted(info.get("models") or []) == sorted(wanted), "a key this operator holds allows other models"


def env_dict(env) -> dict:
    return dict(e.split("=", 1) for e in env)


def env_differs(left: dict, right: dict) -> list:
    """Which names differ. Names only: a value here could be a secret."""
    return sorted(k for k in set(left) | set(right) if left.get(k) != right.get(k))


def only_env(before: dict, after: dict, named: dict) -> None:
    """What compose would run differs by these services' named environment, and by nothing else on any service."""
    b, a = copy.deepcopy(before.get("services", {})), copy.deepcopy(after.get("services", {}))
    assert set(a) == set(b), "the services would change"
    for service, env in named.items():
        got, was = a[service].get("environment") or {}, b[service].get("environment") or {}
        for name, value in env.items():
            assert got.pop(name, None) == value, f"not rendered as planned: {service} {name}"
            was.pop(name, None)
    bad = D._differing_fields(a, b)
    assert not bad, "changed beyond the named environment, at: " + ", ".join(bad[:8])
    bad = D._differing_fields({k: v for k, v in after.items() if k != "services"}, {k: v for k, v in before.items() if k != "services"})
    assert not bad, "changed beyond its services, at: " + ", ".join(bad[:8])


def _without_named(contract, names):
    """A contract with the named environment taken out, in the two shapes an env field can have."""
    out = json.loads(json.dumps(contract))
    env = out.get("env") if isinstance(out, dict) else None
    if isinstance(env, list):
        out["env"] = sorted(e for e in env if str(e).split("=", 1)[0] not in names)
    elif isinstance(env, dict):
        out["env"] = {k: v for k, v in env.items() if k not in names}
    return out


def check_contract(saved: dict, live: dict, names, where: str) -> None:
    left, right = D._same_meaning(_without_named(saved, names)), D._same_meaning(_without_named(live, names))
    assert left == right, f"contract of {where} changed beyond the named environment, at: " + ", ".join(D._differing_fields(left, right)[:8])


def _agents(config: dict):
    return [config["agents"]["defaults"], *config["agents"]["entries"].values()]


def check_routed_mc_policy(live: dict, want: dict, secrets=()) -> None:
    """D.check_mc_policy, with the one deliberate change: the capped gateway provider, by reference to the key, and its tiers allowed."""
    text = json.dumps(live)
    assert not any(s and s in text for s in secrets), "a key value is written in MC's runtime config"
    providers = (live.get("models") or {}).get("providers") or {}
    assert providers == {GATEWAY: want["provider"]}, "MC's API provider is not the planned one"
    assert want["provider"]["apiKey"] == "${MC_MODEL_GATEWAY_KEY}", "MC's API provider does not read its capped key by reference"
    _check_agents(live, want, "MC")
    rest = copy.deepcopy(live)
    rest["models"]["providers"] = {}
    D.check_mc_policy(rest)


def check_cos_runtime(live: dict, want: dict, secrets=()) -> None:
    text = json.dumps(live)
    assert not any(s and s in text for s in secrets), "a key value is written in CoS's runtime config"
    providers = (live.get("models") or {}).get("providers") or {}
    assert set(providers) == {GATEWAY}, "CoS's API provider is not the planned one"
    got, planned = providers[GATEWAY], want["provider"]        # OpenClaw may add its own defaults to a model: compare what was planned
    assert got.get("apiKey") == "${MINIMOI_MODEL_GATEWAY_KEY}" == planned["apiKey"], "CoS's API provider does not read its key by reference"
    assert got.get("baseUrl") == planned["baseUrl"], "CoS's API provider points elsewhere"
    assert sorted(m["id"] for m in got.get("models", [])) == sorted(m["id"] for m in planned["models"]), "CoS's API provider holds other tiers"
    _check_agents(live, want, "CoS")


def _check_agents(live: dict, want: dict, who: str) -> None:
    for agent in _agents(live):
        assert agent["model"]["primary"] == want["primary"], who + "'s default model moved"
        assert not agent["model"].get("fallbacks"), who + " has a model fallback"
        assert agent["modelPolicy"]["allow"] == want["allow"], who + "'s allowlist is not the planned one"
        assert want["primary"] != GROK_REF, "Grok is never a default"


def override_doc(env_by_service: dict) -> dict:
    return {"services": {service: {"environment": dict(sorted(env.items()))} for service, env in sorted(env_by_service.items())}}


def keygen_source(kind: str, models, helpers=None) -> str:
    """The Python to run inside the gateway, from mc_keys.sh's own helper, for this exact model list. Env in: ROUTES_CAP, ROUTES_OLD_KEY."""
    helpers = helpers or pathlib.Path(__file__).with_name("mc_keys.sh")
    prefix = {"mc": ("$MC_KEY_ROUTES", "$MC_KEY_BUDGET_DURATION", "$MC_KEY_RPM", "mc-routes-", "guild.mc"),
              "cos": ("$COS_KEY_ROUTES", "$COS_KEY_BUDGET_DURATION", "$COS_KEY_RPM", "cos-agent-routes-", "guild.cos")}[kind]
    script = (f'source "$1"; gateway_keygen_py "$2" "{prefix[0]}" "{prefix[1]}" "{prefix[2]}" {prefix[3]} {prefix[4]} ROUTES_CAP ROUTES_OLD_KEY')
    return subprocess.run(["bash", "-c", script, "bash", str(helpers), json.dumps(list(models))], capture_output=True, text=True, check=True, timeout=30).stdout


def cos_base_models(helpers=None) -> list:
    helpers = helpers or pathlib.Path(__file__).with_name("mc_keys.sh")
    return json.loads(subprocess.run(["bash", "-c", 'source "$1"; printf %s "$COS_KEY_MODELS"', "bash", str(helpers)], capture_output=True, text=True, check=True, timeout=30).stdout)


# Run inside the gateway. By the key's sha256, so no key value sits in a URL or an access log.
KEY_INFO_PY = r'''
import hashlib, json, os, urllib.request as u
digest = hashlib.sha256(os.environ["ROUTES_KEY"].strip().encode()).hexdigest()
req = u.Request("http://127.0.0.1:4000/key/info?key=" + digest, headers={"Authorization": "Bearer " + os.environ["LITELLM_MASTER_KEY"]})
info = json.loads(u.urlopen(req, timeout=30).read()).get("info") or {}
print(json.dumps({f: info.get(f) for f in ("models", "max_budget", "budget_duration", "spend", "key_alias", "blocked")}))
'''

KEY_UPDATE_PY = r'''
import json, os, urllib.request as u
body = {"key": os.environ["ROUTES_KEY"].strip(), "models": json.loads(os.environ["ROUTES_MODELS"])}
req = u.Request("http://127.0.0.1:4000/key/update", data=json.dumps(body).encode(), method="POST",
                headers={"Authorization": "Bearer " + os.environ["LITELLM_MASTER_KEY"], "Content-Type": "application/json"})
u.urlopen(req, timeout=30).read()
print("updated")
'''

# Run inside the gateway, reading its own config: the tiers exist, their provider key is set, and no fallback chain touches them.
GATEWAY_CHECK_PY = r'''
import json, os, yaml
config = yaml.safe_load(open("/app/config.yaml"))
want = set(json.loads(os.environ["ROUTES_WANT"]))
rows = {m["model_name"]: m.get("litellm_params") or {} for m in config["model_list"]}
no_key = sorted({p["api_key"][len("os.environ/"):] for n, p in rows.items()
                 if n in want and str(p.get("api_key", "")).startswith("os.environ/") and not os.environ.get(p["api_key"][len("os.environ/"):])})
chains = set()
for section in ("router_settings", "litellm_settings"):
    for name, value in (config.get(section) or {}).items():
        if "fallbacks" in name:
            for row in value or []:
                for source, targets in row.items():
                    chains.update([source, *targets])
print(json.dumps({"missing": sorted(want - set(rows)), "no_provider_key": no_key, "in_a_fallback": sorted(want & chains), "key_database": bool(os.environ.get("DATABASE_URL"))}))
'''

# Run in the running portal image, sealed: that image's own code must accept the new catalogue and names the relay's routes.
RELAY_ROUTES_PY = r'''
import json, pathlib, sys
from core.model_routes import load_catalogue, relay_routes
path = pathlib.Path("/tmp/catalogue.json")
path.write_text(sys.stdin.read())
print(json.dumps(relay_routes(load_catalogue(path)), separators=(",", ":"), sort_keys=True))
'''

# Run in the running MC and CoS images, sealed, with a placeholder key: that image's own policy names what its runtime will hold.
MC_POLICY_JS = r'''
const p = require(process.env.ROUTES_POLICY_FILE);
p.mode(process.env);
const o = p.overlay(process.env), d = o.agents.defaults;
if (!p.failoverRoutes(process.env).length) throw Error('no failover target');
console.log(JSON.stringify({provider: o.models.providers['minimoi-gateway'], allow: d.modelPolicy.allow, primary: d.model.primary, fallbacks: d.model.fallbacks}));
'''

COS_POLICY_JS = r'''
const fs = require('fs'), p = require(process.env.ROUTES_POLICY_FILE);
const o = p.apply(JSON.parse(fs.readFileSync(process.env.ROUTES_PINNED_CONFIG, 'utf8')), process.env), d = o.agents.defaults;
if (!p.failoverRoutes(process.env).length) throw Error('no failover target');
console.log(JSON.stringify({provider: o.models.providers['minimoi-gateway'], allow: d.modelPolicy.allow, primary: d.model.primary, fallbacks: d.model.fallbacks}));
'''

MC_POLICY_FILE, COS_POLICY_FILE, COS_PINNED_CONFIG = D.MC_DIR + "model-access.cjs", D.COS_DIR + "plan-access.cjs", D.COS_DIR + "openclaw.json"
# Code each running image must already hold; this operator ships no image.
CODE_MARKS = ((MC, "MC_FAILOVER_ROUTES", MC_POLICY_FILE), (MC, "mc-tool-starts", D.MC_DIR + "start.sh"), (RELAY, "MC_RELAY_TOOL_STARTS", D.MC_DIR + "relay_v2.mjs"),
              (COS, "COS_FAILOVER_ROUTES", COS_POLICY_FILE), ("minimoi-portal", "_check_failover", "/app/core/model_routes/__init__.py"),
              ("minimoi-cos-scheduler", "_check_failover", "/app/core/model_routes/__init__.py"))


def revoke_words(path) -> str:
    return (f"To revoke it: K=$(cat {path}) docker exec -e K {GATEWAY_CONTAINER} python -c \"import json,os,urllib.request as u;"
            "u.urlopen(u.Request('http://127.0.0.1:4000/key/delete',data=json.dumps({'keys':[os.environ['K']]}).encode(),"
            "headers={'Authorization':'Bearer '+os.environ['LITELLM_MASTER_KEY'],'Content-Type':'application/json'}))\""
            f" && rm {path}")


# ── The order of things ──

class RouteSwitch:
    """``host`` does the work, so every failure can be tested without a host."""

    def __init__(self, host, guarded_swap):
        self.host, self.guarded_swap = host, guarded_swap

    def _prepare(self):
        self.host.check_baseline()
        self.host.check_disk()

    def dry_run(self) -> dict:
        self._prepare()
        return self.host.plan()

    def apply(self):
        h = self.host
        self._prepare()
        h.plan()
        h.save_before()
        h.ensure_keys()                             # before anything stops: a refusal here costs no downtime

        def forward(gate):
            h.write_override()
            h.recreate(gate)                        # the runtime knows the routes before any catalogue line offers them
            h.write_catalogue()
            h.verify_new()
        outcome = self._swap(forward, h.restore_verified)
        try:
            h.verify_workers()
        except BaseException as exc:
            h.record("workers_failed_verification", {"exception": type(exc).__name__})
            try:
                self.rollback()
            except BaseException as rollback_exc:
                h.record("rollback_after_worker_failure_failed", {"exception": type(rollback_exc).__name__})
            raise
        h.record("result", {"status": "routes_on_verified"})
        return outcome

    def rollback(self):
        h = self.host
        h.check_saved()

        def no_second_try(_):                       # a rollback that failed is not restored: workers must stay stopped
            raise RuntimeError("rollback failed; recovery required")
        outcome = self._swap(h.restore_verified, no_second_try)
        h.verify_workers()
        h.say_revoke()
        h.record("result", {"status": "rolled_back_verified"})
        return outcome

    def _swap(self, forward, restore):
        h = self.host
        return self.guarded_swap(stop_workers=h.stop_workers, start_workers=h.start_workers, wait_idle=h.wait_idle, gate=h.gate,
                                 gate_alive=h.gate_alive, apply=forward, restore=restore, record=h.record)


class RealHost:
    def __init__(self, root=D.STAGING_ROOT, op_module=None, providers=(DEFAULT_FAILOVER,)):
        self.R = pathlib.Path(root)
        D.refuse_production(self.R)
        self.OP = self.R / "operators" / OPERATOR_NAME
        D.refuse_production(self.OP)
        self.BEFORE, self.KEYS = self.OP / "before", self.OP / "keys"
        self.CAT = self.R / "config/model-routes/catalogue.json"
        self.WRAPPERS = {"compose": self.R / "bin/compose.sh", "mc": self.R / "bin/mc.sh"}
        self.OVERRIDES = {"compose": self.OP / "compose.routes.yml", "mc": self.OP / "mc.routes.yml"}
        self.MC_KEY_FILE, self.COS_KEY_FILE = self.KEYS / "mc-gateway.key", self.KEYS / "cos-gateway.key"
        if op_module is None:                       # pragma: no cover  (the host's own)
            sys.path.insert(0, str(self.R / "operators/mc-route-roles-20261105"))
            import op as op_module
        self.O = op_module
        self.providers = providers
        self.secrets, self.notes, self.where = set(), [], {}

    # plumbing
    def scrub(self, text: str) -> str:
        for secret in sorted(self.secrets, key=len, reverse=True):
            if secret:
                text = text.replace(secret, "<redacted>")
        return text

    def say(self, text: str) -> None:
        print(self.scrub(text), flush=True)

    def _private(self, path, text):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(path, 0o600)

    def _run(self, *args, input=None, timeout=900, env=None):
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, input=input, env=env)
        if p.returncode:
            self._private(self.OP / "last-command.private.log", self.scrub(" ".join(map(str, args[:6])) + "\n" + p.stdout + "\n" + p.stderr))
            raise RuntimeError(f"command failed (log kept privately): {args[0]} {args[1] if len(args) > 1 else ''}")
        return p.stdout

    def _compose(self, wrapper, *args):
        return self._run(str(self.WRAPPERS[wrapper]), *args)

    def _render(self, wrapper, override=None):
        extra = ("-f", str(override)) if override else ()
        return json.loads(self._compose(wrapper, *extra, "config", "--format", "json"))

    def _sealed(self, image, entrypoint, *args, input=None, extra=()):
        return self._run("docker", "run", "--rm", "-i", "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m", "--cap-drop", "ALL",
                         "--security-opt", "no-new-privileges", *extra, "--entrypoint", entrypoint, image, *args, input=input)

    def _exec_ok(self, name, *args):
        return subprocess.run(["docker", "exec", name, *args], capture_output=True, text=True, timeout=120).returncode == 0

    def _gateway(self, code, **env):
        """stdin is the code. Values travel by this process's environment, never on a command line."""
        names = [x for name in env for x in ("-e", name)]
        return self._run("docker", "exec", "-i", *names, GATEWAY_CONTAINER, "python", "-", input=code, env={**os.environ, **env}, timeout=120)

    def record(self, name, detail):
        line = self.scrub(json.dumps({"at": time.time(), "event": name, **detail}))
        with (self.OP / "journal.jsonl").open("a") as f:
            f.write(line + "\n")
        if name == "recovery_required":
            (self.OP / "recovery-required.json").write_text(self.scrub(json.dumps({"at": time.time(), **detail})))
        if name in ("result", "outcome"):
            (self.OP / (name + ".json")).write_text(self.scrub(json.dumps({"at": time.time(), **detail})))

    def _note(self, text):
        self.notes.append(text)
        self.say("NOTE " + text)

    def _env(self, name):
        return env_dict(self.O.inspect(name)["Config"]["Env"])

    def _key_info(self, key):
        self.secrets.add(key)
        return json.loads(self._gateway(KEY_INFO_PY, ROUTES_KEY=key).strip().splitlines()[-1])

    def _key_update(self, key, models):
        self.secrets.add(key)
        assert self._gateway(KEY_UPDATE_PY, ROUTES_KEY=key, ROUTES_MODELS=json.dumps(models)).strip().endswith("updated"), "the gateway did not update the key"

    # baseline: reads only
    def _refuse_if_production(self, env: dict, where: str):
        # FLASK_ENV and NODE_ENV are framework modes, production on every host. Dev's portal inherits the library value from the base compose file.
        ignored = {"FLASK_ENV", "NODE_ENV", "MINIMOI_LIBRARY_INTAKE_ENV"}
        if env.get("MINIMOI_ROLE") == "production" or any(k.endswith("_ENV") and k not in ignored and v in ("production", "prod") for k, v in env.items()):
            raise Refused(f"{where} says production; nothing changed")

    def _locate(self):
        renders = {w: self._render(w) for w in self.WRAPPERS}
        where = {}
        for name, _, _ in ORDER:
            found = [(w, s) for w, r in renders.items() for s, c in r["services"].items() if c.get("container_name") == name]
            assert len(found) == 1, f"{name} is not in exactly one compose project"
            where[name] = found[0]
        return renders, where

    def check_baseline(self):
        assert self.R.is_dir() and self.OP.is_dir(), "not the Dev host"
        assert not (self.OP / "recovery-required.json").exists(), "an earlier run needs recovery"
        assert not (self.R / "operators" / BETA_OPERATOR / "recovery-required.json").exists(), "the 1.2 deploy needs recovery first"
        assert not any(o.exists() for o in self.OVERRIDES.values()), "already applied: roll back first"
        for wrapper in self.WRAPPERS.values():
            D.refuse_production_text(wrapper.read_text(), wrapper.name)
        portal = self.O.inspect("minimoi-portal")
        assert (portal["Config"].get("Labels") or {}).get("com.docker.compose.project") == "minimoi-staging", "the portal is not Dev's"
        self._refuse_if_production(env_dict(portal["Config"]["Env"]), "the portal")
        self.renders, self.where = self._locate()
        for wrapper, render in self.renders.items():
            D.refuse_production_sources(render, [s for w, s in self.where.values() if w == wrapper])
        for name, _, _ in ORDER:
            self._check_running_as_rendered(name)
        for name in (n for n in D.REQUIRED_ENV if n in self.O.snapshot()):
            D.check_required_env(name, self.O.inspect(name)["Config"]["Env"])
        self._check_model_access()
        self._check_code()
        old = json.loads(self.CAT.read_text())
        self.old_catalogue = old
        self.new_catalogue, self.catalogue_lines = plan_catalogue(old, self.providers)
        self.env_plan = route_env(self.new_catalogue)
        self._check_catalogue_readers(hashlib.sha256(self.CAT.read_bytes()).hexdigest())
        self._check_gateway()
        self._plan_relay()
        self._plan_policies()
        self._plan_keys()
        self._check_render_with(PLACEHOLDER_KEY)
        D.check_mc_policy(json.loads((self._mc_state() / "openclaw.json").read_text()))

    def _check_running_as_rendered(self, name):
        """A recreate must change only what this operator named: what compose would run today is what is running."""
        wrapper, service = self.where[name]
        live, rendered = self.O.inspect(name), self.renders[wrapper]["services"][service]
        assert rendered.get("image") == live["Config"]["Image"], "compose would not run what is running: " + name
        D.check_tag_is_running(live["Config"]["Image"], json.loads(self._run("docker", "image", "inspect", live["Config"]["Image"]))[0]["Id"], live["Image"])
        env = env_dict(live["Config"]["Env"])
        drift = sorted(k for k, v in (rendered.get("environment") or {}).items() if v is not None and env.get(k) != v)
        if drift:
            raise Refused(f"{name}: running environment differs from compose's, at: {', '.join(drift[:8])}. Nothing changed.")
        self._refuse_if_production(env, name)

    def _check_model_access(self):
        cos, mc = self._env(COS), self._env(MC)
        if cos.get("COS_RUNTIME_MODEL_ACCESS") != "plan":
            raise Refused("CoS is not in plan mode, so failover has nothing to fail over from. Nothing changed.")
        if mc.get("MC_RUNTIME_MODEL_ACCESS", "plan") != "plan" or not mc.get("MC_PLAN_MODEL"):
            raise Refused("MC is not in plan mode with MC_PLAN_MODEL set, which metered routes require. Nothing changed.")
        forbidden = [n for n in FORBIDDEN_MC_ENV if mc.get(n)]
        if forbidden:
            raise Refused("MC holds a provider credential, which its start refuses: " + ", ".join(forbidden))
        if not mc.get("MC_TOOLS_TOKEN"):
            raise Refused("MC's tools are off, and failover's tool evidence needs them. Nothing changed.")
        live = self.O.inspect(RELAY)
        if "relay_v2.mjs" not in " ".join([*(live["Config"].get("Entrypoint") or []), *(live["Config"].get("Cmd") or [])]):
            raise Refused("the relay does not run relay_v2, which alone reports plan signals. Nothing changed.")

    def _check_code(self):
        missing = [f"{name}: {mark}" for name, mark, path in CODE_MARKS if not self._exec_ok(name, "grep", "-q", mark, path)]
        if missing:
            raise Refused("the running images lack this code, and this operator ships no image: " + "; ".join(missing))
        if not self._exec_ok(MC, "grep", "-rlq", "toolStartMarker", "/opt/minimoi"):
            self._note("no tool-start marker found under /opt/minimoi in MC's image. If it is elsewhere, fine; if it is absent, every switch stops on doubt (safe, but failover never happens).")

    def _catalogue_sha_in(self, name):
        path = self._env(name).get("MINIMOI_MODEL_ROUTES_FILE")
        assert path, name + " names no route catalogue"
        return D.parse_sums(self._run("docker", "exec", name, "sha256sum", path)).get(path)

    def _check_catalogue_readers(self, want):
        for name in CATALOGUE_READERS:
            assert self._catalogue_sha_in(name) == want, f"{name} does not read the host's catalogue file"

    def _check_gateway(self):
        models = sorted({r.split("/")[1] for env in self.env_plan.values() for v in env.values() for r in v.split(",") if r})
        got = json.loads(self._gateway(GATEWAY_CHECK_PY, ROUTES_WANT=json.dumps(models)).strip().splitlines()[-1])
        if got["missing"]:
            raise Refused("the gateway has no tier: " + ", ".join(got["missing"]))
        if got["no_provider_key"]:
            raise Refused("the gateway has no provider key by this name: " + ", ".join(got["no_provider_key"]))
        if got["in_a_fallback"]:
            raise Refused("the gateway's own fallback chain touches: " + ", ".join(got["in_a_fallback"]))
        if not got["key_database"]:
            raise Refused("the gateway has no key database, so no capped key can be made. Nothing changed.")
        assert self.O.inspect(GATEWAY_CONTAINER)["State"]["Running"], "the gateway is not running"

    def _mc_state(self):
        return pathlib.Path(next(v["Source"] for v in self.O.inspect(MC)["Mounts"] if v["Destination"] == STATE_DEST))

    def _plan_relay(self):
        relay, mc = self.O.inspect(RELAY), self.O.inspect(MC)
        source = next(v for v in mc["Mounts"] if v["Destination"] == STATE_DEST)
        mount = next((v for v in relay["Mounts"] if v.get("Name") == source.get("Name") and v.get("Source") == source.get("Source")), None)
        if mount is None:
            raise Refused("the relay does not mount MC's state, and only env may change here: it could not read tool marks. Nothing changed.")
        env = env_dict(relay["Config"]["Env"])
        chat = env.get("MC_RELAY_CHAT_DB")
        if not chat:
            for candidate in (f"{mount['Destination']}/agents/mc-chat/agent/openclaw-agent.sqlite", f"{mount['Destination']}/agents/mc-chat/openclaw-agent.sqlite"):
                if self._exec_ok(RELAY, "test", "-r", candidate):
                    chat = candidate
                    break
        if not chat:
            raise Refused("no readable conversation record for the relay's MC_RELAY_CHAT_DB. Nothing changed.")
        portal = self.O.inspect("minimoi-portal")["Image"]
        raw = self._sealed(portal, "python", "-c", RELAY_ROUTES_PY, input=catalogue_text(self.new_catalogue), extra=("-e", "PYTHONDONTWRITEBYTECODE=1")).strip().splitlines()[-1]
        check_relay_routes(env.get("MC_RELAY_ROUTES"), raw, self.new_catalogue, self.providers)
        self.env_plan[RELAY] = {"MC_RELAY_ROUTES": raw, "MC_RELAY_TOOL_STARTS": f"{mount['Destination']}/mc-tool-starts", "MC_RELAY_CHAT_DB": chat}

    def _node_policy(self, name, code, policy_file, env, extra_env=()):
        args = [x for k, v in {**env, "ROUTES_POLICY_FILE": policy_file, **dict(extra_env)}.items() for x in ("-e", f"{k}={v}")]
        return json.loads(self._sealed(self.O.inspect(name)["Image"], "node", "-e", code, extra=tuple(args)).strip().splitlines()[-1])

    def _plan_policies(self):
        mc = {k: v for k, v in self._env(MC).items() if k in ("MC_RUNTIME_MODEL_ACCESS", "MC_PLAN_MODEL", "MC_PLAN_ROUTES", "MC_PLAN_AUTH_PROFILE", "MINIMOI_ENV")}
        cos = {k: v for k, v in self._env(COS).items() if k in D.AGENT_ENV}
        self.want_mc = self._node_policy(MC, MC_POLICY_JS, MC_POLICY_FILE, {**mc, **self.env_plan[MC], "MC_MODEL_GATEWAY_KEY": PLACEHOLDER_KEY})
        self.want_cos = self._node_policy(COS, COS_POLICY_JS, COS_POLICY_FILE, {**cos, **self.env_plan[COS], "COS_GATEWAY_KEY_CAPPED": "1"}, (("ROUTES_PINNED_CONFIG", COS_PINNED_CONFIG),))
        for who, want, primary in (("MC", self.want_mc, mc.get("MC_PLAN_MODEL")), ("CoS", self.want_cos, cos.get("COS_PLAN_MODEL"))):
            assert want["primary"] == primary and want["primary"] != GROK_REF and not want["fallbacks"], who + "'s planned default or fallbacks are wrong"

    def _cos_key_value(self):
        """CoS's key as CoS holds it. Kept in this process; reaches the gateway by environment only."""
        if self.cos_key["kind"] == "file":
            path = self._env(COS).get("COS_GATEWAY_KEY_FILE") or COS_KEY_MOUNT + "/key"
            key = self._run("docker", "exec", COS, "head", "-n", "1", path).strip()
        else:
            key = self._env(COS).get("MINIMOI_MODEL_GATEWAY_KEY", "")
        if not KEY.match(key):
            raise Refused("CoS's gateway key is not a gateway virtual key. Nothing changed.")
        self.secrets.add(key)
        return key

    def _plan_keys(self):
        cos_env = self._env(COS)
        master = self._env(GATEWAY_CONTAINER).get("LITELLM_MASTER_KEY", "")
        self.secrets.update({master, cos_env.get("MINIMOI_MODEL_GATEWAY_KEY", "")} - {""})
        mounts = [m["Destination"] for m in self.O.inspect(COS)["Mounts"]]
        self.cos_key = {"kind": classify_cos_key(cos_env, mounts, master)}
        wanted_cos = key_models(self.env_plan[COS]["COS_FAILOVER_ROUTES"])
        if self.cos_key["kind"] == "master":
            self.cos_key.update(budget=CAP_DOLLARS, budget_duration=BUDGET_DURATION, models_after=sorted({*cos_base_models(), *wanted_cos}),
                                state="reuse this operator's kept key" if self.COS_KEY_FILE.exists() else "new")
        else:
            info = self._key_info(self._cos_key_value())
            check_existing_key(info)
            self.cos_key.update(budget=info["max_budget"], budget_duration=info.get("budget_duration"), spend=info.get("spend"), models_before=info["models"],
                                add=sorted(set(wanted_cos) - set(info["models"])), models_after=sorted({*info["models"], *wanted_cos}), state="existing: models extended, budget unchanged",
                                capped_flag_needed=self.cos_key["kind"] == "env" and cos_env.get("COS_GATEWAY_KEY_CAPPED") != "1")
        self.mc_models = key_models(self.env_plan[MC]["MC_FAILOVER_ROUTES"])
        self.mc_key_state = "reuse this operator's kept key" if self.MC_KEY_FILE.exists() else "new"
        if self.MC_KEY_FILE.exists():
            info = self._key_info(self.MC_KEY_FILE.read_text().strip())
            assert info.get("max_budget") == CAP_DOLLARS and info.get("budget_duration") == BUDGET_DURATION, "the kept MC key is not capped as agreed"

    def _named(self, mc_key, cos_key=None):
        """{container: {name: value}} with these key values."""
        named = {name: dict(env) for name, env in self.env_plan.items()}
        named[MC]["MC_MODEL_GATEWAY_KEY"] = mc_key
        if self.cos_key["kind"] == "master":
            named[COS].update({"MINIMOI_MODEL_GATEWAY_KEY": cos_key or PLACEHOLDER_KEY, "COS_GATEWAY_KEY_CAPPED": "1"})
        elif self.cos_key.get("capped_flag_needed"):
            named[COS]["COS_GATEWAY_KEY_CAPPED"] = "1"
        return named

    def _by_wrapper(self, named):
        out = {w: {} for w in self.WRAPPERS}
        for name, env in named.items():
            wrapper, service = self.where[name]
            out[wrapper][service] = env
        return out

    def _check_render_with(self, mc_key, cos_key=None):
        """Into a private file and out again: compose reads the override, and only the named environment moves."""
        for wrapper, env in self._by_wrapper(self._named(mc_key, cos_key)).items():
            tmp = self.OP / f"check.{wrapper}.private.yml"
            self._private(tmp, json.dumps(override_doc(env), indent=2))
            try:
                after = self._render(wrapper, tmp)
            finally:
                tmp.unlink(missing_ok=True)
            only_env(self.renders[wrapper], after, env)

    def check_disk(self):
        D.check_free(shutil.disk_usage(self.OP).free, str(self.OP))

    def plan(self):
        named = self._named("<MC's capped key, from a 0600 file>", "<CoS's capped key, from a 0600 file>")
        diff = catalogue_diff(self.old_catalogue, self.new_catalogue)
        policy = {"mc": {"before": "no API provider", "after": f"models.providers.{GATEWAY} reads ${{MC_MODEL_GATEWAY_KEY}} by reference", "allow": self.want_mc["allow"], "primary": self.want_mc["primary"], "fallbacks": self.want_mc["fallbacks"]},
                  "cos": {"allow": self.want_cos["allow"], "primary": self.want_cos["primary"], "fallbacks": self.want_cos["fallbacks"]}}
        keys = {"mc": {"state": self.mc_key_state, "budget": CAP_DOLLARS, "budget_duration": BUDGET_DURATION, "models": self.mc_models, "file": str(self.MC_KEY_FILE)},
                "cos": dict(self.cos_key)}
        plan = {"operator": OPERATOR_NAME, "failover": list(self.providers), "statement": failover_statement(self.providers), "catalogue": self.catalogue_lines, "catalogue_diff": diff,
                "default_routes": {k: v["route"] for k, v in self.new_catalogue["roles"].items()}, "environment": named, "keys": keys, "policy_change": policy,
                "recreate_in_order": [{"container": n, "down": d} for n, _, d in ORDER], "workers": "stopped for the whole swap, admission gate held, then started",
                "notes": self.notes, "changed": "nothing live"}
        self._private(self.OP / "plan.json", self.scrub(json.dumps(plan, indent=1)))
        self.say(plan["statement"])
        self.say("Grok: selectable pay-per-use API route only. Not a default; no failover names it.")
        for line in self.catalogue_lines:
            self.say("CATALOGUE " + line)
        for line in diff:
            self.say("DIFF " + line)
        for name, env in named.items():
            for key, value in sorted(env.items()):
                self.say(f"ENV {name} {key}={value}")
        self.say(f"KEY MC: {self.mc_key_state}, ${CAP_DOLLARS} per {BUDGET_DURATION}, models {self.mc_models}, file {self.MC_KEY_FILE}")
        if self.cos_key["kind"] == "master":
            self.say(f"KEY CoS: holds the master key today. {self.cos_key['state']} capped key: ${CAP_DOLLARS} per {BUDGET_DURATION}, models {self.cos_key['models_after']}, file {self.COS_KEY_FILE}; replaces the master key on cos-agent-a, including its web search")
        else:
            self.say(f"KEY CoS: own key ({self.cos_key['kind']}). Budget ${self.cos_key['budget']} per {self.cos_key['budget_duration']}, spend {self.cos_key.get('spend')}: unchanged. Adds models {self.cos_key['add']}")
        self.say("POLICY MC: " + json.dumps(policy["mc"]))
        self.say("POLICY CoS: " + json.dumps(policy["cos"]))
        for name, _, down in ORDER:
            self.say(f"WILL RECREATE {name}  down: {down}")
        self.say(f"Rollback removes the override and restores; the key stays valid. {revoke_words(self.MC_KEY_FILE)}")
        self.say("Roll this back BEFORE any rollback of " + BETA_OPERATOR + ": that one restores wrappers and catalogue from before this operator.")
        return plan

    # save
    def _worker_names(self):
        known = self.O.snapshot()
        return [w for w in D.WORKERS if w in known and self.O.inspect(w)["State"]["Running"]]

    def _others(self):
        mine = {n for n, _, _ in ORDER}
        return {k: v for k, v in self.O.snapshot().items() if k not in mine}

    def save_before(self):
        self.BEFORE.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.BEFORE, 0o700)
        for wrapper, path in self.WRAPPERS.items():
            shutil.copy2(path, self.BEFORE / f"wrapper.{wrapper}.private")
        shutil.copy2(self.CAT, self.BEFORE / "catalogue.json")
        state = self._mc_state()
        shutil.copy2(state / "openclaw.json", self.BEFORE / "runtime.private.json")
        os.chmod(self.BEFORE / "runtime.private.json", 0o600)
        saved = {"images": {}, "contracts": {}, "dev_values": {}, "env_names": {}, "state": str(state), "where": self.where, "providers": list(self.providers),
                 "others": self._others(), "workers": self._worker_names(), "renders": self.renders, "cos_key": self.cos_key, "cos_key_changed": False,
                 "catalogue_sha": hashlib.sha256(self.CAT.read_bytes()).hexdigest(), "new_catalogue_sha": hashlib.sha256(catalogue_text(self.new_catalogue).encode()).hexdigest()}
        for name, _, _ in ORDER:
            live = self.O.inspect(name)
            saved["images"][name] = live["Image"]
            saved["contracts"][name] = json.loads(json.dumps(self.O.contract(name)))
            saved["dev_values"][name] = D.dev_values(live["Config"]["Env"])
            saved["env_names"][name] = env_dict(live["Config"]["Env"])
        self.saved = saved
        self._save_state()

    def _save_state(self):
        self._private(self.BEFORE / "state.private.json", json.dumps(self.saved))

    def check_saved(self):
        assert (self.BEFORE / "state.private.json").exists(), "nothing was applied: no saved state"
        self.saved = json.loads((self.BEFORE / "state.private.json").read_text())
        self.where = {k: tuple(v) for k, v in self.saved["where"].items()}
        self.cos_key = self.saved["cos_key"]
        self.secrets.update(path.read_text().strip() for path in (self.MC_KEY_FILE, self.COS_KEY_FILE) if path.exists())
        for wrapper, path in self.WRAPPERS.items():
            kept, override = (self.BEFORE / f"wrapper.{wrapper}.private").read_text(), str(self.OVERRIDES[wrapper])
            if path.read_text() not in (kept, D.with_override(kept, override)):
                raise Refused(f"{path.name} changed since this operator applied; restoring it would lose that. Nothing changed.")
        if hashlib.sha256(self.CAT.read_bytes()).hexdigest() != self.saved["new_catalogue_sha"]:
            shutil.copy2(self.CAT, self.BEFORE / "catalogue.at-rollback.json")
            self.record("catalogue_moved_since_apply", {"kept": "before/catalogue.at-rollback.json"})

    # keys: a value goes to a 0600 file and an override, and nowhere else
    def _make_key(self, kind, models, path):
        self.KEYS.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.KEYS, 0o700)
        out = self._gateway(keygen_source(kind, models), ROUTES_CAP=str(CAP_DOLLARS), ROUTES_OLD_KEY="").splitlines()
        key = out[0].strip() if out else ""
        if not KEY.match(key):
            raise Refused("the gateway's answer was not a key; nothing written")
        self.secrets.add(key)
        tmp = path.with_suffix(".tmp")
        self._private(tmp, key + "\n")
        os.replace(tmp, path)
        self.record("key_made", {"who": kind, "key_id_last4": out[1].strip() if len(out) > 1 else "", "budget": CAP_DOLLARS, "budget_duration": BUDGET_DURATION, "models": models})
        return key

    def _ensure_file_key(self, kind, models, path):
        """Kept from an earlier run, so a re-apply never makes a second $20 key."""
        if path.exists():
            key = path.read_text().strip()
            self.secrets.add(key)
            info = self._key_info(key)
            if sorted(info.get("models") or []) != sorted(models):
                self._key_update(key, models)
                self.record("key_models_set", {"who": kind, "models": models})
        else:
            key = self._make_key(kind, models, path)
        check_new_key(self._key_info(key), models)
        os.chmod(path, 0o600)
        return key

    def ensure_keys(self):
        self.mc_key = self._ensure_file_key("mc", self.mc_models, self.MC_KEY_FILE)
        self.cos_file_key = None
        if self.cos_key["kind"] == "master":
            self.cos_file_key = self._ensure_file_key("cos", self.cos_key["models_after"], self.COS_KEY_FILE)
            assert self.cos_file_key != self.mc_key, "CoS and MC share a key"
        else:
            key = self._cos_key_value()
            assert key != self.mc_key, "CoS and MC share a key"
            before = self._key_info(key)
            check_existing_key(before)
            add = sorted(set(self.cos_key["models_after"]) - set(before["models"]))
            if add:
                self.saved["cos_key_changed"] = True
                self._save_state()
                self._key_update(key, sorted({*before["models"], *add}))
            after = self._key_info(key)
            assert after.get("max_budget") == before["max_budget"] and after.get("budget_duration") == before.get("budget_duration"), "CoS's budget moved"
            assert sorted(after["models"]) == sorted({*before["models"], *add}), "CoS's key does not allow the planned models"
            self.record("cos_key_extended", {"added": add, "budget": before["max_budget"], "budget_duration": before.get("budget_duration")})
        self._check_render_with(self.mc_key, self.cos_file_key)

    # swap
    def stop_workers(self):
        running = self.saved["workers"]
        if running:
            self._run("docker", "stop", "--time", str(D.WORKER_STOP_SECONDS), *running)

    def start_workers(self):
        if self.saved["workers"]:
            self._run("docker", "start", *self.saved["workers"])

    def wait_idle(self):
        self.O.idle()                               # MC's delegations and turns; the admission gate checks them again under lock
        code = ("import os,sqlite3,time;d=sqlite3.connect('file:'+os.environ['COS_TOOL_BINDINGS_FILE']+'?mode=ro',uri=True);"
                "print(d.execute('select count(*) from bindings where revoked is null and expires>?',(time.time(),)).fetchone()[0])")
        end = time.monotonic() + 300
        while time.monotonic() < end:
            if self._run("docker", "exec", "minimoi-cos-scheduler", "python", "-c", code).strip() == "0":
                return
            time.sleep(5)
        raise Refused("CoS still has a live turn; nothing changed")

    @contextmanager
    def gate(self):
        with self.O.admission_gate() as held:
            yield held

    def gate_alive(self, held):
        return self.O.gate_alive(held)

    def _write_file(self, path, text, like):
        stat = like.stat()
        tmp = path.with_suffix(path.suffix + ".routes-tmp")
        tmp.write_text(text)
        os.chmod(tmp, stat.st_mode & 0o777)
        os.chown(tmp, stat.st_uid, stat.st_gid)
        os.replace(tmp, path)

    def write_override(self):
        for wrapper, env in self._by_wrapper(self._named(self.mc_key, self.cos_file_key)).items():
            self._private(self.OVERRIDES[wrapper], json.dumps(override_doc(env), indent=2))
            path = self.WRAPPERS[wrapper]
            self._write_file(path, D.with_override(path.read_text(), str(self.OVERRIDES[wrapper])), path)
        self.record("override_written", {"files": [str(p) for p in self.OVERRIDES.values()]})

    def recreate(self, gate):
        for name, limit, _ in ORDER:
            wrapper, service = self.where[name]
            if name == MC:                          # a SIGKILL before OpenClaw frees its lease costs minutes on the next start
                subprocess.run(["docker", "stop", "--time", "120", name], capture_output=True, timeout=180)
            self._compose(wrapper, "up", "-d", "--no-deps", "--no-build", "--pull", "never", service)
            self.O.wait_healthy(name, gate, limit)
            self.record("recreated", {"container": name})

    def write_catalogue(self):
        self._write_file(self.CAT, catalogue_text(self.new_catalogue), self.CAT)
        self.record("catalogue_written", {"lines": len(self.catalogue_lines)})

    # verify
    def _cos_runtime(self):
        path = self._env(COS).get("OPENCLAW_CONFIG_PATH") or STATE_DEST + "/openclaw.json"
        return json.loads(self._run("docker", "exec", COS, "cat", path))

    def _check_unrelated(self, strict):
        now = self._others()
        moved = sorted(k for k in self.saved["others"] if now.get(k) != self.saved["others"][k])
        if strict:
            assert not moved, "an unrelated container changed: " + ", ".join(moved[:8])
            appeared = sorted(set(now) - set(self.saved["others"]))
            if appeared:                            # compose can hold a short-lived container mid-swap; noted, not fatal
                self.record("others_appeared", {"containers": appeared[:8]})
        elif moved:
            self.record("others_moved_since_apply", {"note": "not this rollback's doing"})

    def verify_new(self):
        named = self._named(self.mc_key, self.cos_file_key)
        for name, _, _ in ORDER:
            live = self.O.inspect(name)
            assert live["State"]["Running"] and live["State"].get("Health", {}).get("Status") in (None, "healthy"), "not healthy: " + name
            assert live["Image"] == self.saved["images"][name], "the image moved: " + name
            env = env_dict(live["Config"]["Env"])
            wrong = sorted(k for k, v in named[name].items() if env.get(k) != v)
            assert not wrong, f"{name} does not run the planned setting, at: {', '.join(wrong)}"
            beyond = [k for k in env_differs(env, self.saved["env_names"][name]) if k not in named[name]]
            assert not beyond, f"{name}'s environment moved beyond the plan, at: {', '.join(beyond[:8])}"
            assert D.dev_values(live["Config"]["Env"]) == self.saved["dev_values"][name], "a Dev setting moved: " + name
            D.check_required_env(name, live["Config"]["Env"])
            check_contract(self.saved["contracts"][name], self.O.contract(name), set(named[name]), name)
        for wrapper, env in self._by_wrapper(named).items():
            only_env(self.saved["renders"][wrapper], self._render(wrapper), env)
        catalogue = json.loads(self.CAT.read_text())
        assert catalogue == self.new_catalogue, "the host catalogue is not the planned one"
        check_new_catalogue(self.old_catalogue, catalogue, self.providers)
        self._check_catalogue_readers(hashlib.sha256(self.CAT.read_bytes()).hexdigest())
        secrets = [s for s in (self.mc_key, self.cos_file_key) if s]
        check_routed_mc_policy(json.loads((pathlib.Path(self.saved["state"]) / "openclaw.json").read_text()), self.want_mc, secrets)
        check_cos_runtime(self._cos_runtime(), self.want_cos, secrets)
        self._check_unrelated(True)
        marks = self._exec_ok(RELAY, "test", "-e", self.env_plan[RELAY]["MC_RELAY_TOOL_STARTS"] + "/.ready")
        door = self._exec_ok(MC, "node", "-e", "const s=require('net').connect(4000,'127.0.0.1');s.setTimeout(5000,()=>process.exit(1));s.on('connect',()=>process.exit(0));s.on('error',()=>process.exit(1))")
        self.record("evidence", {"relay_sees_tool_marks": marks, "mc_reaches_gateway_door": door})
        if not marks:
            self._note("the relay sees no .ready in mc-tool-starts yet: until it does, every switch stops on doubt (safe). Check it after MC's first tool turn.")
        if not door:
            self._note("MC could not open 127.0.0.1:4000, the way to the gateway: API routes on MC will fail until the door answers.")
        self.record("verified", {"containers": [n for n, _, _ in ORDER]})

    def verify_workers(self):
        for name in self.saved["workers"]:
            self.O.wait_healthy(name, None, D.DEFAULT_WAIT)
            assert self.O.inspect(name)["State"]["Running"], "worker not running: " + name

    # restore
    def restore_verified(self, gate):
        self._write_file(self.CAT, (self.BEFORE / "catalogue.json").read_text(), self.CAT)   # nothing offers a route the runtime is about to lose
        for wrapper, path in self.WRAPPERS.items():
            shutil.copy2(self.BEFORE / f"wrapper.{wrapper}.private", path)
        for override in self.OVERRIDES.values():
            override.unlink(missing_ok=True)
        state = pathlib.Path(self.saved["state"])
        subprocess.run(["docker", "stop", "--time", "120", MC], capture_output=True, timeout=180)
        cfg, tmp = state / "openclaw.json", state / "openclaw.json.restore"
        st = cfg.stat()
        shutil.copy2(self.BEFORE / "runtime.private.json", tmp)
        os.chown(tmp, st.st_uid, st.st_gid)
        os.replace(tmp, cfg)
        self.recreate(gate)
        for name, _, _ in ORDER:
            live = self.O.inspect(name)
            assert live["Image"] == self.saved["images"][name], "restored image differs: " + name
            D.check_restored(self.saved["contracts"][name], self.O.contract(name), "contract of " + name)
        for wrapper, path in self.WRAPPERS.items():
            assert path.read_bytes() == (self.BEFORE / f"wrapper.{wrapper}.private").read_bytes(), "restored wrapper differs"
            D.check_restored(self.saved["renders"][wrapper], self._render(wrapper), "compose render")
        assert self.CAT.read_bytes() == (self.BEFORE / "catalogue.json").read_bytes(), "restored catalogue differs"
        self._check_catalogue_readers(self.saved["catalogue_sha"])
        D.check_catalogue(json.loads(self.CAT.read_text()))
        D.same_runtime(json.loads((self.BEFORE / "runtime.private.json").read_text()), json.loads(cfg.read_text()))
        self._check_unrelated(False)
        self._restore_cos_key()
        self.record("restored", {"status": "previous overrides, catalogue, runtime and CoS key models proved"})

    def _restore_cos_key(self):
        if not self.saved.get("cos_key_changed"):
            return
        key = self._cos_key_value()
        self._key_update(key, self.cos_key["models_before"])
        info = self._key_info(key)
        assert sorted(info["models"]) == sorted(self.cos_key["models_before"]), "CoS's key models were not restored"
        assert info.get("max_budget") == self.cos_key["budget"], "CoS's budget moved"

    def say_revoke(self):
        for path in (self.MC_KEY_FILE, self.COS_KEY_FILE):
            if path.exists():
                self.say(f"KEPT {path}: unused now, still valid until revoked; a re-apply reuses it. {revoke_words(path)}")


def main(argv=None):                                # pragma: no cover
    import fcntl
    from guarded_swap import guarded_swap
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("dry-run", "apply", "rollback"))
    parser.add_argument("--failover", default=DEFAULT_FAILOVER)
    args = parser.parse_args(argv)
    host = RealHost(providers=parse_failover(args.failover))
    status = host.OP / "status.json"
    with (host.R / ".renewal-deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)    # refused before status.json: a second start must not overwrite a run's state
        status.write_text(json.dumps({"action": args.action, "state": "running", "at": time.time()}))
        try:
            switch = RouteSwitch(host, guarded_swap)
            result = {"dry-run": switch.dry_run, "apply": switch.apply, "rollback": switch.rollback}[args.action]()
        except BaseException as exc:
            status.write_text(host.scrub(json.dumps({"action": args.action, "state": "failed", "exception": type(exc).__name__, "message": str(exc)[:400], "at": time.time()})))
            print(f"FAILED {type(exc).__name__}: {host.scrub(str(exc))[:400]}", flush=True)
            raise SystemExit(1)                     # no traceback: its text is the one thing not scrubbed
        host.say("RESULT " + (result if isinstance(result, str) else "planned" if isinstance(result, dict) else str(result)))
        status.write_text(json.dumps({"action": args.action, "state": "done", "at": time.time()}))


if __name__ == "__main__":                          # pragma: no cover
    main()
