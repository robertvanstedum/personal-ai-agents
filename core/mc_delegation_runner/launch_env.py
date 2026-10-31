"""The launch contract's one policy (host runtime spec v0.5 R6.7-d), read from one versioned data file,
``launch_policy.json`` beside this module, and its environment step.

The file is read by the delegation runner (``workspace.child_env``, ``launch``), the two front-door connections
(``core/agent_requests/claude_stream.py`` and ``codex_app_server.py``) and the fixed-send probe
(``core/agent_requests/claude_fixed_send.py``). The fallback door (operations' ``scripts/agents/mm_agent.py``) reads
the same file later: its supervised profile is ``child_env(..., profile="supervised")``, and its pins, sign-in checks
and records are ``launch.py``'s. The file's SHA-256 (``POLICY_DIGEST``) goes into every launch record (LCH-6).

* A child's environment is built from the profile's inherited names, never inherited wholesale: a name in the parent
  that is not on the list does not reach the child. A provider key or token in the parent therefore cannot make Claude
  Code or Codex bill the API instead of the plan sign-in.
* Anything else a child needs is passed by its exact name, as an ``extra``, and only if the name is on ``EXTRA_ALLOW``:
  a reviewed list, each name with its reason. Any other extra is refused.
* The deny patterns (credentials, provider and endpoint switches, configuration redirects, code injection) apply to
  every name, inherited or extra. A name a pattern catches passes only when it is on the file's ``deny_overrides`` for
  that way and profile, each entry reviewed: ``MINIMOI_AGENT_REQUESTS_DIR`` as an extra; ``HOME`` inherited only;
  ``SSH_AUTH_SOCK`` inherited, in the supervised profile only. Nothing else overrides a deny pattern.
* The unattended profile (runtime items, connections) can hold no credential-class name and no ``SSH_AUTH_SOCK``; the
  file is refused at load otherwise. The supervised profile (the fallback door, Robert present) adds his own git
  agent, terminal, locale and editor names, and still no provider key or token.
* Refusals name the variable, never its value.

The file is read once, at import, strictly: a damaged or inconsistent policy fails closed (``PolicyRefused``), so no
child can be started without it. It lives in this package because the runner's installed bundle copies only
``core/mc_delegation_runner`` and ``core/mc_delegation_dispatcher`` (``scripts/mc_host/install_delegation_runner.sh``).
Stdlib only; Python 3.9-compatible.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Mapping

POLICY_PATH = Path(__file__).resolve().with_name("launch_policy.json")
SCHEMA = "minimoi.launch_policy.v1"
PROFILES = ("unattended", "supervised")
ROUTE_KINDS = ("subscription", "api", "test")
TEST_FAMILY = ("fakework", "fakejudge")           # the authority's test routes (R6.7-l), by name and dotted sub-route
MAX_BYTES = 256 * 1024
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_ROUTE = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)*$")
_TOOL = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_CLASS = re.compile(r"^[a-z][a-z_]{0,31}$")
_KEY_PATH = re.compile(r"^[A-Za-z0-9_*-]{1,64}(\.[A-Za-z0-9_*-]{1,64}){0,7}$")


class PolicyRefused(ValueError):
    """The policy file cannot be used: a fixed code only."""


class EnvRefused(ValueError):
    """An extra the policy will not pass. The message names the variable, never its value."""


def _need(ok, code):
    if not ok:
        raise PolicyRefused(code)


def _no_duplicates(pairs):
    out = {}
    for k, v in pairs:
        _need(k not in out, "launch_policy_duplicate_key")
        out[k] = v
    return out


def _named_list(items, code):
    """[{name, reason}, ...] → ordered names, each once, each with a reason."""
    _need(type(items) is list, code)
    names = []
    for it in items:
        _need(type(it) is dict and set(it) == {"name", "reason"} and type(it["name"]) is str
              and _NAME.match(it["name"]) and type(it["reason"]) is str and len(it["reason"]) >= 10, code)
        _need(it["name"] not in names, code)
        names.append(it["name"])
    return names


def _template(value, code):
    _need(type(value) is str and 0 < len(value) <= 512 and "\0" not in value
          and (value.startswith(("{home}/", "{project}/")) or value.startswith("/"))
          and ".." not in value.split("/"), code)


def _check_tools(tools):
    _need(type(tools) is dict and tools, "launch_policy_tools_invalid")
    for name, t in tools.items():
        code = f"launch_policy_tool_invalid:{name}"
        _need(_TOOL.match(name) and type(t) is dict, code)
        _need({"login_command", "signin", "config"} <= set(t)
              <= {"login_command", "signin", "config", "ignores_user_config_flag", "test", "note"}, code)
        _need(type(t["login_command"]) is str and 0 < len(t["login_command"]) <= 120, code)
        _need(type(t.get("test", False)) is bool and type(t.get("note", "")) is str, code)
        _need(type(t.get("ignores_user_config_flag", "--x")) is str and t.get("ignores_user_config_flag", "--x")
              .startswith("--"), code)
        _need(type(t["signin"]) is list and type(t["config"]) is list, code)
        for s in t["signin"]:
            _need(type(s) is dict and "file" in s and len(s) == 2 and len(set(s) & {"exists", "nonempty", "equals"}) == 1,
                  code)
            _template(s["file"], code)
            _need(s.get("exists", True) is True and type(s.get("nonempty", "")) is str, code)
            _need("equals" not in s or (type(s["equals"]) is list and len(s["equals"]) == 2
                                       and all(type(x) is str for x in s["equals"])), code)
        for c in t["config"]:
            _need(type(c) is dict and {"file", "scope", "format"} <= set(c)
                  <= {"file", "scope", "format", "keys", "names_anywhere", "values_allowed", "env_tables"}, code)
            _template(c["file"], code)
            _need(c["scope"] in ("user", "project", "managed", "signin") and c["format"] in ("json", "toml"), code)
            _need(c["file"].startswith("{project}/") == (c["scope"] == "project"), code)
            for k in ("keys", "names_anywhere", "env_tables"):
                _need(type(c.get(k, [])) is list and all(type(x) is str and _KEY_PATH.match(x) for x in c.get(k, [])),
                      code)
            va = c.get("values_allowed", {})
            _need(type(va) is dict and all(_KEY_PATH.match(k) and type(v) is list and v and all(type(x) is str for x in v)
                                            for k, v in va.items()), code)


def _check_routes(routes, tools, credential_shaped):
    _need(type(routes) is dict and routes, "launch_policy_routes_invalid")
    for name, r in routes.items():
        code = f"launch_policy_route_invalid:{name}"
        _need(type(name) is str and len(name) <= 64 and _ROUTE.match(name) and type(r) is dict, code)
        _need({"kind", "tools"} <= set(r) <= {"kind", "tools", "key_name", "qualified", "test", "note"}, code)
        _need(r["kind"] in ROUTE_KINDS and type(r["tools"]) is list and set(r["tools"]) <= set(tools), code)
        family = name.split(".", 1)[0] in TEST_FAMILY
        _need(r.get("test", False) is family, code)                     # a test route is one by name, never by flag
        _need(type(r.get("note", "")) is str, code)
        if r["kind"] == "api":
            # The key's own name must be one every other child loses to a credential pattern (R6.7-e).
            _need(type(r.get("key_name")) is str and _NAME.match(r["key_name"]) and credential_shaped(r["key_name"])
                  and type(r.get("qualified")) is bool, code)
        else:
            _need("key_name" not in r and "qualified" not in r, code)


def parse(raw: bytes) -> dict:
    """The policy, checked: every rule the module docstring states holds, or PolicyRefused names which."""
    _need(type(raw) is bytes and len(raw) <= MAX_BYTES, "launch_policy_too_large")
    try:
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except PolicyRefused:
        raise
    except (ValueError, UnicodeDecodeError):
        raise PolicyRefused("launch_policy_unreadable") from None
    _need(type(doc) is dict and set(doc) == {"schema", "version", "note", "base", "extras", "profiles", "deny_patterns",
                                            "deny_overrides", "routes", "tools"}, "launch_policy_invalid")
    _need(doc["schema"] == SCHEMA and type(doc["version"]) is int and doc["version"] >= 1 and type(doc["note"]) is str,
          "launch_policy_schema_invalid")
    base = _named_list(doc["base"], "launch_policy_base_invalid")
    extras = _named_list(doc["extras"], "launch_policy_extras_invalid")
    profiles = doc["profiles"]
    _need(type(profiles) is dict and set(profiles) == set(PROFILES), "launch_policy_profiles_invalid")
    inherit = {}
    for p in PROFILES:
        _need(type(profiles[p]) is dict and set(profiles[p]) == {"used_by", "note", "inherit"}
              and type(profiles[p]["used_by"]) is list and type(profiles[p]["note"]) is str,
              "launch_policy_profiles_invalid")
        added = _named_list(profiles[p]["inherit"], "launch_policy_profiles_invalid")
        _need(not set(added) & set(base), "launch_policy_profiles_invalid")
        inherit[p] = tuple(base + added)
    pats = doc["deny_patterns"]
    _need(type(pats) is list and pats, "launch_policy_deny_invalid")
    compiled = []
    for it in pats:
        _need(type(it) is dict and set(it) == {"pattern", "class"} and type(it["pattern"]) is str
              and type(it["class"]) is str and _CLASS.match(it["class"]), "launch_policy_deny_invalid")
        try:
            compiled.append((re.compile(it["pattern"], re.IGNORECASE), it["class"]))
        except re.error:
            raise PolicyRefused("launch_policy_deny_invalid") from None

    def classes(name):
        return {cls for rx, cls in compiled if rx.search(name)}

    overrides = {"inherited": {p: set() for p in PROFILES}, "extra": {p: set() for p in PROFILES}}
    _need(type(doc["deny_overrides"]) is list, "launch_policy_overrides_invalid")
    for o in doc["deny_overrides"]:
        _need(type(o) is dict and set(o) == {"name", "way", "profiles", "reason"} and type(o["name"]) is str
              and _NAME.match(o["name"]) and o["way"] in overrides and type(o["profiles"]) is list and o["profiles"]
              and set(o["profiles"]) <= set(PROFILES) and type(o["reason"]) is str and len(o["reason"]) >= 10,
              "launch_policy_overrides_invalid")
        _need(classes(o["name"]), f"launch_policy_override_vacuous:{o['name']}")      # only where a pattern catches
        _need("credentials" not in classes(o["name"]), f"launch_policy_override_credential:{o['name']}")
        for p in o["profiles"]:
            overrides[o["way"]][p].add(o["name"])
    for p in PROFILES:
        for n in inherit[p]:
            _need(not classes(n) or n in overrides["inherited"][p], f"launch_policy_inherited_denied:{p}:{n}")
        for n in extras:
            _need(not classes(n) or n in overrides["extra"][p], f"launch_policy_extra_denied:{p}:{n}")
    # The unattended profile holds no credential name at all and no SSH_AUTH_SOCK (R6.7-d's table).
    unattended = set(inherit["unattended"]) | overrides["inherited"]["unattended"] | overrides["extra"]["unattended"]
    _need("SSH_AUTH_SOCK" not in unattended, "launch_policy_unattended_ssh_agent")
    _need(not any("credentials" in classes(n) for n in unattended | set(extras)), "launch_policy_unattended_credential")
    _check_tools(doc["tools"])
    _check_routes(doc["routes"], doc["tools"], lambda n: "credentials" in classes(n))
    return {"doc": doc, "base": tuple(base), "extras": {n: next(e["reason"] for e in doc["extras"] if e["name"] == n)
                                                         for n in extras},
            "inherit": inherit, "patterns": tuple(it["pattern"] for it in pats), "classes": classes,
            "inherit_overrides": {p: frozenset(s) for p, s in overrides["inherited"].items()},
            "extra_overrides": {p: frozenset(s) for p, s in overrides["extra"].items()}}


def load(path=POLICY_PATH) -> tuple:
    """(policy, 'sha256:<hex>') for the file at ``path``; PolicyRefused when it cannot be used."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
    except OSError:
        raise PolicyRefused("launch_policy_unreadable") from None
    return parse(raw), "sha256:" + hashlib.sha256(raw).hexdigest()


POLICY, POLICY_DIGEST = load()
POLICY_VERSION = POLICY["doc"]["version"]
ROUTES = POLICY["doc"]["routes"]
TOOLS = POLICY["doc"]["tools"]

# The names below keep the module's earlier interface (PR #436); the values now come from the file.
BASE_ALLOW = POLICY["base"]
EXTRA_ALLOW = dict(POLICY["extras"])
DENY_PATTERNS = POLICY["patterns"]
_DENY = tuple(re.compile(p, re.IGNORECASE) for p in DENY_PATTERNS)
# Extras a deny pattern catches that are not secrets, reviewed (the unattended profile). Nothing else overrides one.
DENY_OVERRIDES = POLICY["extra_overrides"]["unattended"]
INHERIT = POLICY["inherit"]                          # profile → the names copied from the parent when present
INHERIT_OVERRIDES = POLICY["inherit_overrides"]      # profile → inherited names a deny pattern catches, reviewed


def denied(name: str) -> bool:
    """True when ``name`` matches a deny pattern (before any override is considered)."""
    return any(p.search(name) for p in _DENY)


def credential_shaped(name: str) -> bool:
    """True when ``name`` matches a pattern of the credentials class: what LCH-1 and the key-route rule mean by a key."""
    return "credentials" in POLICY["classes"](name)


def _extra_overrides(profile):
    return DENY_OVERRIDES if profile == "unattended" else POLICY["extra_overrides"][profile]


def check_extra(name, profile: str = "unattended") -> None:
    """Raise ``EnvRefused`` unless ``name`` may be passed to a child by explicit name."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise EnvRefused("environment_name_invalid")
    if name not in EXTRA_ALLOW:
        raise EnvRefused(f"extra_not_reviewed: {name}")
    if denied(name) and name not in _extra_overrides(profile):
        raise EnvRefused(f"credential_variable_refused: {name}")


def child_env(extra: Mapping[str, str] | None = None, *, environ: Mapping[str, str] | None = None,
              profile: str = "unattended") -> dict[str, str]:
    """The environment for one child: the profile's inherited names copied from ``environ`` (default: this process's
    environment) when present and not denied, then each ``extra`` by its exact name. Every extra is checked before
    anything is returned; a refused one raises ``EnvRefused`` and no environment is built."""
    if profile not in PROFILES:
        raise EnvRefused("launch_profile_unknown")
    source = os.environ if environ is None else environ
    names = BASE_ALLOW if profile == "unattended" else INHERIT[profile]
    env = {k: source[k] for k in names if k in source and (not denied(k) or k in INHERIT_OVERRIDES[profile])}
    for name, value in (extra or {}).items():
        check_extra(name, profile)
        if not isinstance(value, str) or "\0" in value:
            raise EnvRefused(f"environment_value_invalid: {name}")
        env[name] = value
    return env


def removed(environ: Mapping[str, str], env: Mapping[str, str]) -> list:
    """The names in the parent that did not reach the child, sorted (a launch record's ``removed``). Names only."""
    return sorted(set(environ) - set(env))
