#!/usr/bin/env python3
"""Dev only: folds the side layers that 31 Oct to 12 Nov sessions added to Dev's compose wrappers into one reviewed override per
wrapper, so the release update's own checks can see what compose runs. Robert, 12 Nov: rebuild Dev clean, record every dropped
change, keep only what a working Dev feature needs that the release does not already carry.

``plan`` (on the Dev host) reads only. It parses bin/compose.sh and bin/mc.sh, classifies every ``-f`` file as base, reviewed,
fold or side, proves each image-only side layer superseded, and refuses on any side setting the keep list below does not name.
``fold --execute`` (on the host, under the deploy lock) backs up what it changes (0600), writes ``compose.fold.yml`` and
``mc.fold.yml`` with the kept settings copied host-side, rewrites both wrappers to base + beta + routes + fold, and proves with
``docker compose config`` that every service renders exactly as before except its image, which becomes the beta override's.
It recreates nothing: the next release update does that. ``rollback`` restores the backed-up files exactly and proves the render.
``host-script`` (on the Mac) writes a bash script for ssm_run.sh that carries this file, checks its hash and runs one action.

Values never leave the host: plan, journal, DROPPED.md and errors carry setting names, never values. Never touches production
(/opt/minimoi), containers, the routes operator's saved state, keys, catalogues or images. Python 3.9 and PyYAML (the host's)."""
from __future__ import annotations

import argparse
import base64
import copy
import gzip
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

try:
    import yaml
except ImportError:                                 # pragma: no cover  (the host and the venv both have it)
    yaml = None

STAGING_ROOT = "/opt/minimoi-staging"
STAGING_INSTANCE = "i-0ad80f79eff0a7c4f"
BETA = "beta-dev-20261106"
ROUTES = "beta-dev-routes-20261106"
REVIEWED = {"compose": (f"{BETA}/compose.beta.yml", f"{ROUTES}/compose.routes.yml"),
            "mc": (f"{BETA}/mc.beta.yml", f"{ROUTES}/mc.routes.yml")}
WRAPPERS = {"compose": "bin/compose.sh", "mc": "bin/mc.sh"}
FOLD_PREFIX = "dev-fold-"
FOLD_FILES = {"compose": "compose.fold.yml", "mc": "mc.fold.yml"}
LOCK = ".renewal-deploy.lock"
IMAGE_KEYS = ("image", "pull_policy")
UPDATE_IN_FLIGHT = "update-in-flight.json"


class Refused(RuntimeError):
    """Stopped before anything live changed."""


# ── The keep list: Dev's 14 feature groups (DEV_LAYER_CATALOG_2026-11-12), by setting name ──
# A setting is "service:env:NAME", "service:volume:/target", "service:volumes:!override", "service:key:NAME" or "top:KEY".
# A side layer may only set what its own group names here; anything else stops the plan until someone decides it.

def _env(service, *names):
    return [f"{service}:env:{n}" for n in names]


def _vol(service, *targets):
    return [f"{service}:volume:{t}" for t in targets]


def _key(service, *names):
    return [f"{service}:key:{n}" for n in names]


_INC1_AGENT_VOLUMES = ("/home/node/.openclaw", "/home/node/.config/openclaw", "/minimoi/data", "/home/node/.openclaw/workspace-cos-agent-a/mc-evidence",
                       "/minimoi/docs", "/minimoi/scripts", "/minimoi/agent_logs", "/minimoi/cos/inbox", "/minimoi/cos/outbox", "/minimoi/cos/conversations",
                       "/minimoi/planning-studio", "/minimoi/repo", "/minimoi/mc/work", "/minimoi/mc/workshops", "/minimoi/mc/artifacts", "/run/cos-key",
                       "/run/secrets/cos-bound")
_SERVICE_SHAPE = ("cap_drop", "command", "container_name", "cpus", "mem_limit", "networks", "read_only", "restart", "security_opt", "tmpfs")
_LEAD_RETURN_COMMON = ("HELPER_LEAD_REVIEW_RESERVE_USD", "LEAD_RETURN_CALL_BOUND_USD", "LEAD_RETURN_COMMIT_TOKEN_FILE", "LEAD_RETURN_EFFORT",
                       "LEAD_RETURN_GATEWAY_KEY_FILE", "LEAD_RETURN_GATEWAY_URL", "LEAD_RETURN_HELPER_STATE_DIRS", "LEAD_RETURN_INGRESS_TOKEN_FILE",
                       "LEAD_RETURN_MODEL", "LEAD_RETURN_PRINCIPAL", "LEAD_RETURN_READ_TOKEN_FILE", "LEAD_RETURN_ROUTE_ID", "LEAD_RETURN_STATE_DIR",
                       "PARTNERSHIP_CHARTER_APPROVALS", "PARTNERSHIP_ENVIRONMENT", "PARTNERSHIP_TASKS_URL")

GROUPS = (
    dict(id=1, feature="CoS store environment and MC everywhere",
         layers=("minimoi12-20261031-v3", "minimoi12-20261031-v4", "minimoi12-20261031-v5"),
         settings=(*_env("cos-scheduler", "COS_STORE_ENVIRONMENT"), *_env("portal", "MINIMOI_MC_EVERYWHERE"))),
    dict(id=2, feature="Voice session allowance", layers=("cos-voice-allowance-20261031", "cos-voice-restore-20261102"),
         settings=(*_env("cos-scheduler", "MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO"), *_env("portal", "MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO"))),
    dict(id=3, feature="Records portal key", layers=("minimoi12-20261031-v9",), settings=_env("portal", "RECORDS_PORTAL_KEY_FILE")),
    dict(id=4, feature="Guildhall for CoS", layers=("minimoi12-20261031-v10",),
         settings=_env("cos-scheduler", "COS_GUILDHALL_DIR", "COS_GUILDHALL_TOKEN_FILE", "MINIMOI_GUILDHALL_COS")),
    dict(id=5, feature="CoS actions", layers=("cos-actions-20261102",),
         settings=(*_env("cos-scheduler", "COS_ACTIONS_TOKEN_FILE", "COS_STORE_DIR"), *_vol("cos-scheduler", "/var/lib/minimoi/cos-actions", "/run/secrets/cos-actions-token"),
                   *_env("cos-agent-a", "COS_ACTIONS_TOKEN_FILE"), *_vol("cos-agent-a", "/run/secrets/cos-actions-token"))),
    dict(id=6, feature="MC and Rooms bridge", layers=("mc-room-bridge-deploy-20261102",),
         settings=_env("portal", "MINIMOI_MC_ROOMS", "MINIMOI_MC_ROOMS_STATE_DIR", "MINIMOI_MC_ROOMS_TOKEN_SHA256", "MINIMOI_MC_ROOMS_WORK_TOKEN_FILE")),
    dict(id=7, feature="CoS agent role", layers=("cos-role-20261102",), settings=_env("cos-agent-a", "COS_AGENT_ROLE")),
    dict(id=8, feature="CoS Board mount", layers=("cos-board-20261102",), settings=_vol("cos-agent-a", "/run/secrets/cos-board-profile.json")),
    # The catalog names partnership-inc2 here. partnership-inc1 is the same feature's first increment: it defines the
    # partnership-tasks service itself, so it cannot be dropped; its settings are listed with inc2's.
    dict(id=9, feature="Partnership and plan routes", layers=("partnership-inc1-20261105", "partnership-inc2-20261105"),
         settings=(
             # inc1
             *_env("portal", "MINIMOI_JOBS_DIR", "MINIMOI_JOBS_HOST", "PARTNERSHIP_ENVIRONMENT", "PARTNERSHIP_TASKS_URL", "PORTAL_COS_OWNER_ENV",
                   "PORTAL_COS_OWNER_KEY_FILE", "PORTAL_PARTNERSHIP_OWNER_ENV", "PORTAL_PARTNERSHIP_OWNER_KEY_FILE", "PORTAL_PARTNERSHIP_PRINCIPAL_TOKEN_FILE"),
             *_vol("portal", "/run/secrets/partnership", "/app/runtime/jobs"), *_key("portal", "networks"),
             *_env("cos-scheduler", "COS_BOARD_PROFILE_FILE", "COS_BOARD_URL", "COS_MEETING_OBSERVE_TOKEN_FILE", "COS_MEETING_RECORDS_URL",
                   "COS_OWNER_ASSERTION_ENV", "COS_OWNER_ASSERTION_KEY_FILE", "COS_OWNER_ASSERTION_NONCE_FILE", "COS_OWNER_PRINCIPAL",
                   "COS_TASKS_COS_TOKEN_FILE", "COS_TASKS_PLATFORM_TOKEN_FILE", "COS_TOOL_BINDINGS_FILE", "COS_TOOL_LISTENER_PORT",
                   "COS_TOOL_LISTENER_TOKEN_SHA256", "MINIMOI_VAULT_ASK_CONVERSATIONS", "MINIMOI_VAULT_ASK_INCLUDE_RAW", "MINIMOI_VAULT_ASK_INTERNAL_HOSTS",
                   "MINIMOI_VAULT_ASK_TOKEN", "MINIMOI_VAULT_ASK_URL", "PARTNERSHIP_ENVIRONMENT", "PARTNERSHIP_TASKS_URL"),
             *_vol("cos-scheduler", "/run/secrets/partnership", "/app/runtime/partnership"), *_key("cos-scheduler", "networks"),
             "cos-agent-a:volumes:!override", *_vol("cos-agent-a", *_INC1_AGENT_VOLUMES),
             *_key("partnership-tasks", *_SERVICE_SHAPE, "healthcheck"),
             *_env("partnership-tasks", "PARTNERSHIP_BINDINGS", "PARTNERSHIP_ENVIRONMENT", "PARTNERSHIP_OWNER_ENV", "PARTNERSHIP_OWNER_KEY_FILE",
                   "PARTNERSHIP_OWNER_NONCE_FILE", "PARTNERSHIP_OWNER_SUBJECT", "PARTNERSHIP_PORT", "PARTNERSHIP_ROOT", "PYTHONPATH"),
             *_vol("partnership-tasks", "/data", "/run/secrets/partnership"),
             "top:networks",
             # inc2, compose
             *_env("portal", "MINIMOI_ACTION_NOTICES_AGENT_TOKENS", "MINIMOI_GUILD_AGENT_REQUESTS", "MINIMOI_MODEL_ROUTES_FILE",
                   "MINIMOI_SUPERVISION_WORKER_TOKEN_SHA256", "PARTNERSHIP_CARD_REF", "PARTNERSHIP_EXECUTOR_PROFILES", "PARTNERSHIP_MC_TOKEN_FILE"),
             *_vol("portal", "/run/minimoi-model-routes"),
             *_key("partnership-worker", *_SERVICE_SHAPE),
             *_env("partnership-worker", "DATABASE_URL_FILE", "MINIMOI_MC_DELEGATION_AUTHORITY_FILE", "MINIMOI_MC_DELEGATION_ENV",
                   "MINIMOI_MC_DELEGATION_PRINCIPAL", "PARTNERSHIP_DRAINER_TOKEN_FILE", "PARTNERSHIP_ENVIRONMENT", "PARTNERSHIP_NOTICES_TOKEN_FILE",
                   "PARTNERSHIP_NOTICES_URL", "PARTNERSHIP_RUNNER_SEATS_FILE", "PARTNERSHIP_TASKS_URL", "PARTNERSHIP_WORKER_LOCK", "PYTHONPATH"),
             *_vol("partnership-worker", "/data", "/run/secrets/partnership", "/run/minimoi/runtime/authority.dev.json"),
             *_env("cos-agent-a", "COS_PLAN_AUTH_PROFILE", "COS_PLAN_MODEL", "COS_PLAN_ROUTES", "COS_RUNTIME_MODEL_ACCESS"),
             *_env("cos-scheduler", "MINIMOI_MODEL_ROUTES_FILE"), *_vol("cos-scheduler", "/run/minimoi-model-routes"),
             # inc2, mc
             *_env("mc-agent", "MC_MODEL_GATEWAY_KEY", "MC_PLAN_AUTH_PROFILE", "MC_PLAN_MODEL", "MC_PLAN_ROUTES", "MC_RUNTIME_MODEL_ACCESS"),
             *_env("mc-relay", "MC_RELAY_CHAT_DB", "MC_RELAY_ROUTES"))),
    dict(id=10, feature="Depth proof", layers=("work-modes-depth-v5-20261111",), decision="depth_proof",
         settings=(*_env("cos-agent-a", "MINIMOI_DEPTH_PROOF", "MINIMOI_RUNTIME_PROOF_MODULE"), *_env("mc-agent", "MINIMOI_DEPTH_PROOF", "MINIMOI_RUNTIME_PROOF_MODULE"),
                   *_env("mc-relay", "MC_RELAY_DEPTH_PROOFS"))),
    dict(id=11, feature="Metered routes (a 4th xAI route)", layers=("work-modes-depth-v7-20261111",), decision="metered",
         settings=(*_env("cos-agent-a", "COS_METERED_ROUTES"), *_env("mc-agent", "MC_METERED_ROUTES"), *_env("mc-relay", "MC_RELAY_ROUTES"))),
    dict(id=12, feature="Work artifacts", layers=("work-modes-w2-v2-20261111",), settings=_env("partnership-tasks", "PARTNERSHIP_WORK_ARTIFACTS")),
    dict(id=13, feature="Helper and lead return, owner accounts", layers=("work-modes-w3-20261111", "cos-owner-accounts-20261111"),
         settings=(*_env("partnership-tasks", "PARTNERSHIP_HELPER_RETURNS"),
                   *_env("portal", *_LEAD_RETURN_COMMON, "LEAD_RETURN_ACCESS_TOKEN_FILE", "LEAD_RETURN_OWNER_ACCOUNT", "MC_HELPER_RETURN"),
                   *_vol("portal", "/app/runtime/lead-return", "/app/runtime/helper-state/apprentice", "/run/secrets/lead-return"),
                   *_env("cos-scheduler", *_LEAD_RETURN_COMMON, "COS_EXECUTION_LANE_DIR", "COS_HELPER_RETURN", "COS_LEAD_WAKE",
                         "LEAD_RETURN_OWNER_ACCESS_TOKEN_FILE", "LEAD_RETURN_OWNER_ACCESS_URL", "LEAD_RETURN_OWNER_ACCOUNTS"),
                   *_vol("cos-scheduler", "/app/runtime/lead-return", "/app/runtime/helper-state/ea", "/run/secrets/lead-return"),
                   *_env("partnership-worker", "PARTNERSHIP_COS_RETURN_TOKEN_FILE", "PARTNERSHIP_COS_RETURN_URL", "PARTNERSHIP_HELPER_RETURN_TRANSPORT",
                         "PARTNERSHIP_MC_RETURN_TOKEN_FILE", "PARTNERSHIP_MC_RETURN_URL"),
                   *_vol("partnership-worker", "/run/secrets/lead-return"))),
    dict(id=14, feature="Career-sync mounts", layers=("career-sync-mounts-20261111",),
         settings=(*_env("cos-scheduler", "COS_WORK_SOURCE_ROOTS_FILE"), *_vol("cos-scheduler", "/minimoi/private/professional"),
                   *_vol("cos-agent-a", "/minimoi/private/professional"))),
)

# An image the reviewed beta override does not name: data-gaps-507-510 moved model-gateway to its own build. The update tool
# treats the gateway as config only, so the image is picked here, on purpose, with --gateway-image keep|base.
GATEWAY_LAYER, GATEWAY_SERVICE = "data-gaps-507-510-20261112", "model-gateway"

DECISIONS = {"depth_proof": {"on": "keep", "off": "drop"}, "metered": {"depth-v7": "keep", "routes": "drop"}}
DEFAULT_DECISIONS = {"depth_proof": "on", "metered": "depth-v7"}


# ── Compose files with !override and !reset ──

class Tagged:
    """A compose value carrying !override or !reset. Equal by tag and value."""

    def __init__(self, tag, value):
        self.tag, self.value = tag, value

    def __eq__(self, other):
        return isinstance(other, Tagged) and (self.tag, self.value) == (other.tag, other.value)

    def __repr__(self):
        return f"Tagged({self.tag})"


def _loader():
    class Loader(yaml.SafeLoader):
        pass

    def make(tag):
        def construct(loader, node):
            if isinstance(node, yaml.SequenceNode):
                return Tagged(tag, loader.construct_sequence(node, deep=True))
            if isinstance(node, yaml.MappingNode):
                return Tagged(tag, loader.construct_mapping(node, deep=True))
            return Tagged(tag, loader.construct_scalar(node))
        return construct
    for tag in ("!override", "!reset"):
        Loader.add_constructor(tag, make(tag))
    return Loader


def _dumper():
    class Dumper(yaml.SafeDumper):
        pass

    def represent(dumper, data):
        if isinstance(data.value, list):
            return dumper.represent_sequence(data.tag, data.value)
        if isinstance(data.value, dict):
            return dumper.represent_mapping(data.tag, data.value)
        return dumper.represent_scalar(data.tag, str(data.value))
    Dumper.add_representer(Tagged, represent)
    return Dumper


def load_layer(text: str) -> dict:
    try:
        doc = json.loads(text)
    except ValueError:
        doc = yaml.load(text, Loader=_loader())
    if doc is None:
        return {}
    if not isinstance(doc, dict):
        raise Refused("a layer is not a compose mapping")
    return doc


def dump_layer(doc: dict) -> str:
    return yaml.dump(doc, Dumper=_dumper(), default_flow_style=False, sort_keys=False, allow_unicode=True, width=10_000)


# ── Wrappers ──

_F = re.compile(r'(?<!\[ )-f\s+("?)([^\s"]+)\1(?=[\s])')


def wrapper_files(text: str) -> list:
    """Every compose file a wrapper passes with -f, in order. ``[ -f x ]`` tests are not compose files."""
    return [m.group(2) for m in _F.finditer(text)]


def operator_ref(path: str, root: str):
    """'<operator>/<file>' for a file under <root>/operators/, else None."""
    for prefix in (root.rstrip("/") + "/operators/", "$R/operators/", "${R}/operators/"):
        if path.startswith(prefix):
            rest = path[len(prefix):]
            if rest.count("/") == 1:
                return rest
    return None


def classify_wrapper(text: str, wrapper: str, root: str) -> dict:
    base, reviewed, fold, side = [], [], [], []
    for path in wrapper_files(text):
        ref = operator_ref(path, root)
        if ref is None:
            base.append(path)
        elif ref in REVIEWED[wrapper]:
            reviewed.append(ref)
        elif ref.startswith(FOLD_PREFIX) and ref.endswith("/" + FOLD_FILES[wrapper]):
            fold.append(ref)
        else:
            side.append(ref)
    if tuple(reviewed) != REVIEWED[wrapper]:
        raise Refused(f"{WRAPPERS[wrapper]} does not run the reviewed beta and routes overrides once each, in that order")
    if len(set(side)) != len(side):
        raise Refused(f"{WRAPPERS[wrapper]} names a side layer twice")
    return {"base": base, "reviewed": reviewed, "fold": fold, "side": side}


def routes_index(text: str, wrapper: str, root: str) -> int:
    """Where the routes override sits among the operator files, so a setting knows whether it was before or after it."""
    refs = [operator_ref(p, root) for p in wrapper_files(text)]
    refs = [r for r in refs if r]
    return refs.index(REVIEWED[wrapper][1])


STDIN = "/dev/stdin"


def new_wrapper_text(text: str, wrapper: str, root: str, fold_ref: str) -> str:
    """base + beta + routes + fold. Every base line stays byte for byte; only operator -f entries move."""
    out = text
    for path in wrapper_files(text):
        if operator_ref(path, root) is not None:
            hits = out.count(f" -f {path}")
            if hits != 1:
                raise Refused(f"{WRAPPERS[wrapper]}: an operator file is not named exactly once")
            out = out.replace(f" -f {path}", "", 1)
    if out.count('"$@"') != 1:
        raise Refused(f"{WRAPPERS[wrapper]}: wrapper shape (one \"$@\")")
    ops = root.rstrip("/") + "/operators/"
    chain = " ".join(f"-f {ops}{ref}" for ref in REVIEWED[wrapper]) + (f" -f {STDIN}" if fold_ref == STDIN else f" -f {ops}{fold_ref}")
    return out.replace('"$@"', f'{chain} "$@"')


def refuse_production(path) -> None:
    norm = os.path.normpath(str(path))
    if norm == "/opt/minimoi" or norm.startswith("/opt/minimoi/"):
        raise Refused("production path refused: " + norm)


def refuse_production_text(text: str, where: str) -> None:
    if re.search(r"/opt/minimoi(?![-\w])", text):
        raise Refused("production path named in " + where)


# ── Settings: what a layer sets, by name ──

def env_map(env) -> dict:
    if env is None:
        return {}
    if isinstance(env, dict):
        return dict(env)
    out = {}
    for item in env:
        name, _, value = str(item).partition("=")
        out[name] = value if "=" in str(item) else None
    return out


def volume_target(entry) -> str:
    if isinstance(entry, dict):
        return str(entry.get("target"))
    parts = str(entry).split(":")
    return parts[1] if len(parts) >= 2 else parts[0]


def layer_settings(doc: dict) -> tuple:
    """(settings, images). settings: [identity]; images: {service: image}. Names only."""
    settings, images = [], {}
    for key in sorted(doc):
        if key != "services":
            settings.append(f"top:{key}")
    for service, cfg in (doc.get("services") or {}).items():
        for key, value in (cfg or {}).items():
            if key == "image":
                images[service] = value
            elif key == "pull_policy":
                continue
            elif key == "environment":
                settings += [f"{service}:env:{n}" for n in env_map(value)]
            elif key == "volumes":
                items = value
                if isinstance(value, Tagged):
                    settings.append(f"{service}:volumes:{value.tag}")
                    items = value.value or []
                settings += [f"{service}:volume:{volume_target(v)}" for v in (items or [])]
            else:
                settings.append(f"{service}:key:{key}")
    return settings, images


def group_of(layer_op: str, identity: str):
    for group in GROUPS:
        if layer_op in group["layers"] and identity in group["settings"]:
            return group
    return None


def plan_layers(classified: dict, docs: dict, beta_images: dict, decisions: dict) -> dict:
    """The whole plan, by name. classified: {wrapper: classify_wrapper()}; docs: {'op/file': layer doc}; beta_images: {wrapper: {service: image}}."""
    rows, unknown, unsuperseded = [], [], []
    for wrapper, c in classified.items():
        side = c["side"]
        for i, ref in enumerate(side):
            op = ref.split("/")[0]
            settings, images = layer_settings(docs[ref])
            groups = set()
            for identity in settings:
                group = group_of(op, identity)
                if group is None:
                    unknown.append(f"{ref}: {identity}")
                else:
                    groups.add(group["id"])
            superseded = {}
            for service in sorted(images):
                later = [r for r in side[i + 1:] if service in (layer_settings(docs[r])[1])]
                if service in beta_images[wrapper]:
                    superseded[service] = REVIEWED[wrapper][0]
                elif later:
                    superseded[service] = later[0]
                elif op == GATEWAY_LAYER and service == GATEWAY_SERVICE:
                    superseded[service] = "decision: --gateway-image"
                else:
                    unsuperseded.append(f"{ref}: {service}")
            kind = "settings" if settings else "image-only" if images else "empty"
            rows.append({"wrapper": wrapper, "layer": ref, "session": op, "kind": kind, "groups": sorted(groups),
                         "settings": settings, "image_services": sorted(images), "image_superseded_by": superseded})
    counts = {}
    for row in rows:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    groups = []
    for group in GROUPS:
        decision = group.get("decision")
        choice = decisions.get(decision) if decision else None
        groups.append({"id": group["id"], "feature": group["feature"], "layers": list(group["layers"]),
                       "keep": (DECISIONS[decision][choice] == "keep") if decision else True,
                       "decision": f"{decision}={choice}" if decision else None,
                       "present": sorted({r["layer"] for r in rows if group["id"] in r["groups"]})})
    return {"rows": rows, "counts": counts, "unknown": unknown, "unsuperseded": unsuperseded, "groups": groups,
            "base": {w: c["base"] for w, c in classified.items()}, "fold_present": {w: c["fold"] for w, c in classified.items()}}


def check_plan(plan: dict) -> None:
    if plan["unknown"]:
        raise Refused(f"{len(plan['unknown'])} side setting(s) the keep list does not classify; decide them first: " + "; ".join(plan["unknown"][:12]))
    if plan["unsuperseded"]:
        raise Refused("image(s) nothing supersedes; decide them first: " + "; ".join(plan["unsuperseded"][:12]))


# ── The fold: kept settings merged in wrapper order, as compose would merge them ──

def kept_groups(decisions: dict) -> set:
    out = set()
    for group in GROUPS:
        decision = group.get("decision")
        if not decision or DECISIONS[decision][decisions[decision]] == "keep":
            out.add(group["id"])
    return out


def _routes_names(routes_doc: dict) -> dict:
    return {s: set(env_map((c or {}).get("environment"))) for s, c in (routes_doc.get("services") or {}).items()}


def build_fold(wrapper: str, side: list, routes_at: int, docs: dict, routes_doc: dict, decisions: dict, gateway: str) -> tuple:
    """(fold doc, dispositions). routes_at: how many side layers come before the routes override in the wrapper.
    dispositions: [(layer, identity, fate, detail)] with names only. The fold sits after routes, so a setting routes also sets
    stays out of it when it came before routes (routes wins today) and goes in when it came after (it wins today)."""
    keep = kept_groups(decisions)
    shadow = _routes_names(routes_doc)
    services, top, fates = {}, {}, []
    writer = {}                                         # identity -> layer that set it last
    for i, ref in enumerate(side):
        op, doc = ref.split("/")[0], docs[ref]
        before_routes = i < routes_at
        for key, value in doc.items():
            if key == "services":
                continue
            ident = f"top:{key}"
            if group_of(op, ident)["id"] not in keep:
                fates.append((ref, ident, "dropped", "decision"))
                continue
            merged = top.setdefault(key, {})
            if not isinstance(value, dict):
                raise Refused(f"{ref}: top-level {key} is not a mapping")
            for name, item in value.items():
                if name in merged and merged[name] != item:
                    raise Refused(f"{ref}: top-level {key}.{name} set twice, differently")
                merged[name] = copy.deepcopy(item)
            writer[ident] = ref
        for service, cfg in (doc.get("services") or {}).items():
            svc = services.setdefault(service, {"environment": {}, "volumes": {}, "override": False, "keys": {}})
            for key, value in (cfg or {}).items():
                if key in IMAGE_KEYS:
                    if op == GATEWAY_LAYER and service == GATEWAY_SERVICE and gateway == "keep":
                        svc["keys"][key] = copy.deepcopy(value)
                        writer[f"{service}:key:{key}"] = ref
                    continue
                if key == "environment":
                    for name, item in env_map(value).items():
                        ident = f"{service}:env:{name}"
                        if group_of(op, ident)["id"] not in keep:
                            fates.append((ref, ident, "dropped", "decision"))
                            continue
                        if before_routes and name in shadow.get(service, ()):
                            fates.append((ref, ident, "shadowed", "the routes override sets it after this layer"))
                            continue
                        svc["environment"][name] = item
                        writer[ident] = ref
                elif key == "volumes":
                    items = value
                    if isinstance(value, Tagged):
                        if value.tag != "!override":
                            raise Refused(f"{ref}: {service} volumes carry {value.tag}, which the fold does not merge")
                        for ident in [w for w in writer if w.startswith(f"{service}:volume:")]:
                            fates.append((writer.pop(ident), ident, "replaced", f"{ref} replaces {service}'s volumes (!override)"))
                        svc["volumes"], svc["override"] = {}, True
                        items = value.value or []
                    for entry in items or []:
                        ident = f"{service}:volume:{volume_target(entry)}"
                        if group_of(op, ident)["id"] not in keep:
                            fates.append((ref, ident, "dropped", "decision"))
                            continue
                        svc["volumes"][volume_target(entry)] = copy.deepcopy(entry)
                        writer[ident] = ref
                else:
                    ident = f"{service}:key:{key}"
                    if group_of(op, ident)["id"] not in keep:
                        fates.append((ref, ident, "dropped", "decision"))
                        continue
                    if key in svc["keys"] and svc["keys"][key] != value:
                        raise Refused(f"{ref}: {service} {key} set twice, differently")
                    svc["keys"][key] = copy.deepcopy(value)
                    writer[ident] = ref
    out = {"services": {}}
    for service in sorted(services):
        svc, body = services[service], {}
        for key, value in svc["keys"].items():
            body[key] = value
        if svc["environment"]:
            body["environment"] = dict(sorted(svc["environment"].items()))
        if svc["volumes"] or svc["override"]:
            items = list(svc["volumes"].values())
            body["volumes"] = Tagged("!override", items) if svc["override"] else items
        if body:
            out["services"][service] = body
    for key in sorted(top):
        out[key] = top[key]
    carried = set(writer.items())
    for i, ref in enumerate(side):
        settings, _ = layer_settings(docs[ref])
        for ident in settings:
            if ident.endswith(":volumes:!override"):
                continue
            if (ident, ref) in carried:
                fates.append((ref, ident, "carried", "in the fold"))
            elif ident in writer and not any(f[0] == ref and f[1] == ident for f in fates):
                fates.append((ref, ident, "superseded", f"{writer[ident]} sets it later"))
    return out, fates


# ── The proof: the new render equals the old but for the images the beta override names ──

def _strip(service: dict, dropped_env=(), dropped_volumes=()) -> dict:
    out = copy.deepcopy(service)
    for key in IMAGE_KEYS:
        out.pop(key, None)
    env = out.get("environment")
    if isinstance(env, dict):
        for name in dropped_env:
            env.pop(name, None)
    if isinstance(out.get("volumes"), list) and dropped_volumes:
        out["volumes"] = [v for v in out["volumes"] if volume_target(v) not in dropped_volumes]
    return out


def differing_fields(left, right, path="") -> list:
    """Which fields differ, by name only: a value here could be a secret."""
    if isinstance(left, dict) and isinstance(right, dict):
        return [f for key in sorted(set(left) | set(right)) for f in differing_fields(left.get(key), right.get(key), f"{path}/{key}")]
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        return [f for i, (a, b) in enumerate(zip(left, right)) for f in differing_fields(a, b, f"{path}[{i}]")]
    return [] if left == right else [path or "/"]


def value_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def check_equivalent(before: dict, after: dict, beta_images: dict, image_free=(), dropped=()) -> dict:
    """before, after: `docker compose config --format json`. beta_images: {service: image}. image_free: services whose image
    may move to what the base files say. dropped: identities deliberately not carried. Returns names and counts only."""
    b, a = before.get("services") or {}, after.get("services") or {}
    if set(a) != set(b):
        raise Refused("the services would change: " + ", ".join(sorted(set(a) ^ set(b))))
    images, compared = {}, 0
    for service in sorted(a):
        drop_env = [d.split(":", 2)[2] for d in dropped if d.startswith(f"{service}:env:")]
        drop_vol = [d.split(":", 2)[2] for d in dropped if d.startswith(f"{service}:volume:")]
        sa, sb = _strip(a[service], drop_env, drop_vol), _strip(b[service], drop_env, drop_vol)
        bad = differing_fields(sa, sb)
        if bad:
            raise Refused(f"{service} would change beyond its image, at: " + ", ".join(bad[:8]))
        for name, value in (sa.get("environment") or {}).items():
            assert value_hash(value) == value_hash((sb.get("environment") or {}).get(name)), "value hash moved"
            compared += 1
        if service in beta_images:
            if a[service].get("image") != beta_images[service]:
                raise Refused(f"{service} would not run the beta override's image")
            images[service] = "beta override" if b[service].get("image") != a[service].get("image") else "beta override (unchanged)"
        elif service in image_free:
            images[service] = "moved (decided)" if b[service].get("image") != a[service].get("image") else "unchanged"
        elif (a[service].get("image"), a[service].get("pull_policy")) != (b[service].get("image"), b[service].get("pull_policy")):
            raise Refused(f"{service}'s image would move, and nothing decided that")
        else:
            images[service] = "unchanged"
    rest = lambda d: {k: v for k, v in d.items() if k != "services"}
    bad = differing_fields(rest(after), rest(before))
    if bad:
        raise Refused("the project would change beyond its services, at: " + ", ".join(bad[:8]))
    return {"services": len(a), "env_values_compared": compared, "images": images}


# ── DROPPED.md: names only ──

def dropped_text(plan: dict, fates: dict, date: str, decisions: dict, gateway: str, backup: str) -> str:
    lines = [f"# Dev layers folded on {date}", "",
             "Written by scripts/staging/dev_layer_fold.py. Robert, 12 Nov: rebuild Dev clean, record every dropped change, keep only what a",
             "working Dev feature needs that the release does not already carry. Names only: no values. The removed layer files stay where they",
             f"were under operators/, and a copy of each changed file is in {backup}/ (0600).", "",
             f"Decisions: depth proof {decisions['depth_proof']}, metered routes {decisions['metered']}, gateway image {gateway}.", "",
             "Every layer below is gone from the wrappers. Its images are superseded by the reviewed beta override (the release's images) or",
             "by a later layer; its settings are either carried into the fold, superseded by a later layer, shadowed by the routes override,",
             "replaced by a later !override, or dropped by a decision.", "",
             "To redo an image change: ship its commit through the release update (docs/runbooks/dev_fast_update.md), never a side layer.",
             "To redo a dropped setting: add the name to a reviewed Dev override, its value read on the host from the layer file named here.", ""]
    for row in plan["rows"]:
        lines.append(f"## {row['layer']} ({row['wrapper']})")
        lines.append("")
        lines.append(f"- Session: `{row['session']}`. Kind: {row['kind']}." + (f" Groups: {', '.join(map(str, row['groups']))}." if row["groups"] else ""))
        for service, by in sorted(row["image_superseded_by"].items()):
            lines.append(f"- Image of `{service}`: dropped, superseded by `{by}`." if not by.startswith("decision")
                         else f"- Image of `{service}`: {'kept in the fold' if gateway == 'keep' else 'dropped: the base files name the gateway image'} (--gateway-image {gateway}).")
        for ref, ident, fate, detail in fates.get(row["layer"], []):
            lines.append(f"- `{ident}`: {fate} ({detail}).")
        lines.append("")
    return "\n".join(lines)


# ── The host ──

class Host:
    """Reads and writes under one staging root. ``render(script_text, stdin)`` runs a wrapper's text with ``config --format json``,
    reading /dev/stdin as the fold where the candidate names it; tests replace it."""

    def __init__(self, root=STAGING_ROOT, date=None, render=None, clock=time.time, running=None):
        self.R = pathlib.Path(root)
        refuse_production(self.R)
        self.OPS = self.R / "operators"
        self.clock = clock
        self.date = date or time.strftime("%Y%m%d", time.gmtime(clock()))
        if not re.fullmatch(r"\d{8}", self.date):
            raise Refused("--date is YYYYMMDD")
        self.FOLD = self.OPS / f"{FOLD_PREFIX}{self.date}"
        refuse_production(self.FOLD)
        self._render_fn = render
        self._running_fn = running

    # plumbing
    def say(self, text: str) -> None:
        print(text, flush=True)

    def private(self, path: pathlib.Path, text: str) -> None:
        tmp = path.with_name(path.name + ".fold-tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    def private_dir(self, path: pathlib.Path) -> None:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path, 0o700)

    def replace_like(self, path: pathlib.Path, text: str, like_stat) -> None:
        """A wrapper keeps its own mode and owner."""
        tmp = path.with_name(path.name + ".fold-tmp")
        tmp.write_text(text)
        os.chmod(tmp, like_stat.st_mode & 0o7777)
        try:
            os.chown(tmp, like_stat.st_uid, like_stat.st_gid)
        except PermissionError:                     # pragma: no cover  (tests run unprivileged on files they own)
            pass
        os.replace(tmp, path)

    def record(self, event: str, detail: dict) -> None:
        self.private_dir(self.FOLD)
        with (self.FOLD / "journal.jsonl").open("a") as f:
            f.write(json.dumps({"at": self.clock(), "event": event, **detail}) + "\n")
        os.chmod(self.FOLD / "journal.jsonl", 0o600)

    def render(self, script_text: str, stdin=None) -> dict:
        """What compose would run. Read-only: ``docker compose config`` from the wrapper's text, the fold on stdin if named."""
        if self._render_fn is not None:
            return self._render_fn(script_text, stdin)
        p = subprocess.run(["bash", "-c", script_text, "wrapper", "config", "--format", "json"], input=stdin or "",   # pragma: no cover
                           capture_output=True, text=True, timeout=300)
        if p.returncode:                            # pragma: no cover
            log = self.FOLD / "last-render.private.log"
            if self.FOLD.is_dir():                  # plan writes nothing: its log stays unwritten
                self.private(log, p.stdout + "\n" + p.stderr)
            raise Refused("docker compose config failed" + (" (log kept privately)" if self.FOLD.is_dir() else ""))
        return json.loads(p.stdout)                 # pragma: no cover

    def running_image(self, container: str):
        """The tag a container was created from, or None. Read-only."""
        if self._running_fn is not None:
            return self._running_fn(container)
        p = subprocess.run(["docker", "inspect", "-f", "{{.Config.Image}}", container], capture_output=True, text=True, timeout=60)  # pragma: no cover
        return p.stdout.strip() if p.returncode == 0 else None     # pragma: no cover

    def running_vs_compose(self, renders: dict) -> dict:
        """Containers whose running tag is not what compose would now run: the next update's 'compose would not run what is
        running' check names these until they are recreated. Names and tags only."""
        out = {}
        for wrapper, render in renders.items():
            for service, cfg in sorted((render.get("services") or {}).items()):
                name = cfg.get("container_name")
                if not name:
                    continue
                live = self.running_image(name)
                if live is not None and live != cfg.get("image"):
                    out[name] = {"wrapper": wrapper, "service": service, "running": live, "compose_after_fold": cfg.get("image")}
        return out

    def render_wrapper(self, name: str) -> dict:
        return self.render(self.wrapper(name).read_text())

    def wrapper(self, name) -> pathlib.Path:
        return self.R / WRAPPERS[name]

    def layer_path(self, ref: str) -> pathlib.Path:
        return self.OPS / ref

    # reading
    def read_all(self):
        texts, classified, docs = {}, {}, {}
        for name in WRAPPERS:
            text = self.wrapper(name).read_text()
            refuse_production_text(text, WRAPPERS[name])
            texts[name] = text
            classified[name] = classify_wrapper(text, name, str(self.R))
            for ref in classified[name]["side"] + classified[name]["reviewed"]:
                docs[ref] = load_layer(self.layer_path(ref).read_text())
        beta = {w: {s: c.get("image") for s, c in (docs[REVIEWED[w][0]].get("services") or {}).items() if c.get("image")} for w in WRAPPERS}
        return texts, classified, docs, beta

    def check_quiet(self):
        """Nothing else is mid-change on Dev."""
        for op in (BETA, ROUTES):
            if (self.OPS / op / "recovery-required.json").exists():
                raise Refused(f"{op} needs recovery first; nothing changed")
        if (self.OPS / BETA / UPDATE_IN_FLIGHT).exists():
            raise Refused("an update is in flight; nothing changed")

    def plan(self, decisions: dict, prove_gateway=None) -> dict:
        texts, classified, docs, beta = self.read_all()
        plan = plan_layers(classified, docs, beta, decisions)
        if prove_gateway:
            check_plan(plan)
            plan["proofs"] = self.prove(texts, classified, docs, beta, decisions, prove_gateway)[0]
        plan["decisions"] = dict(decisions)
        plan["fold_dir"] = str(self.FOLD)
        plan["changed"] = "nothing"
        self.say(json.dumps({k: v for k, v in plan.items() if k != "rows"}, indent=1))
        for row in plan["rows"]:
            self.say(json.dumps(row, separators=(",", ":")))
        return plan

    # folding
    def fold(self, decisions: dict, gateway) -> dict:
        if gateway not in ("keep", "base"):
            raise Refused("--gateway-image keep|base is required: pick the model-gateway image on purpose")
        self.check_quiet()
        texts, classified, docs, beta = self.read_all()
        plan = plan_layers(classified, docs, beta, decisions)
        if not any(c["side"] for c in classified.values()):
            if all(c["fold"] for c in classified.values()):
                self.say("RESULT already folded: no side layers; nothing changed")
                return {"status": "already_folded"}
            raise Refused("no side layers and no fold: nothing to do")
        if any(c["fold"] for c in classified.values()):
            raise Refused("side layers sit beside an earlier fold: roll that fold back first; nothing changed")
        check_plan(plan)
        state_path = self.FOLD / "state.private.json"
        if state_path.exists() and json.loads(state_path.read_text()).get("status") == "folded":
            raise Refused(f"{self.FOLD.name} already holds a fold the wrappers no longer run; look before folding again")

        # 1. Today's truth, and the candidate proved with the fold on stdin, before anything is written.
        proofs, fold_docs, fates, dropped, before = self.prove(texts, classified, docs, beta, decisions, gateway)
        image_free = {GATEWAY_SERVICE} if gateway == "base" else set()
        self.private_dir(self.FOLD)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self.clock()))
        backup = self.FOLD / f"backup-{stamp}"
        self.private_dir(backup)
        for name, render in before.items():
            self.private(backup / f"render.{name}.private.json", json.dumps(render, sort_keys=True))

        # 2. The fold files: nothing runs them until the wrappers name them.
        fold_paths = {name: self.FOLD / FOLD_FILES[name] for name in WRAPPERS}
        new_texts = {}
        for name in WRAPPERS:
            self.private(fold_paths[name], dump_layer(fold_docs[name]))
            new_texts[name] = new_wrapper_text(texts[name], name, str(self.R), f"{self.FOLD.name}/{FOLD_FILES[name]}")
            refuse_production_text(new_texts[name], "the new " + WRAPPERS[name])

        # 3. Back up, then swap the wrappers.
        files = {}
        for name in WRAPPERS:
            path = self.wrapper(name)
            copy_to = backup / f"wrapper.{name}.private"
            shutil.copy2(path, copy_to)
            os.chmod(copy_to, 0o600)
            st = path.stat()
            files[str(path)] = {"backup": str(copy_to), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                "mode": st.st_mode & 0o7777, "uid": st.st_uid, "gid": st.st_gid}
        state = {"status": "folding", "at": self.clock(), "backup": str(backup), "files": files, "created": [str(p) for p in fold_paths.values()],
                 "decisions": decisions, "gateway": gateway, "new_sha256": {}}
        self.private(state_path, json.dumps(state, indent=1))
        self.record("backed_up", {"backup": str(backup), "files": sorted(files)})
        try:
            for name in WRAPPERS:
                path = self.wrapper(name)
                self.replace_like(path, new_texts[name], path.stat())
                state["new_sha256"][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            # 4. The same proof on the live wrappers.
            for name in WRAPPERS:
                proofs[name] = check_equivalent(before[name], self.render_wrapper(name), beta[name], image_free, dropped[name])
        except BaseException as exc:
            self.record("fold_failed_restoring", {"exception": type(exc).__name__})
            self._restore(state, before)
            state["status"] = "restored_after_failure"
            self.private(state_path, json.dumps(state, indent=1))
            raise
        state["status"] = "folded"
        self.private(state_path, json.dumps(state, indent=1))
        self.private(self.FOLD / "DROPPED.md", dropped_text(plan, fates, self.date, decisions, gateway, backup.name))
        self.private(self.FOLD / "plan.json", json.dumps({**{k: v for k, v in plan.items()}, "proofs": proofs, "decisions": decisions, "gateway": gateway}, indent=1))
        self.record("folded", {"proofs": proofs, "layers_removed": sum(len(c["side"]) for c in classified.values())})
        self.say(json.dumps({"folded": {w: len(c["side"]) for w, c in classified.items()}, "fold_dir": str(self.FOLD), "backup": str(backup),
                             "proofs": proofs, "dropped": dropped, "recreated": "nothing: the next release update recreates"}, indent=1))
        self.say("RESULT folded and proved; no container recreated")
        return {"status": "folded", "proofs": proofs, "backup": str(backup)}

    def prove(self, texts, classified, docs, beta, decisions, gateway) -> tuple:
        """(proofs, fold docs, fates, dropped, before renders). Read-only: the fold reaches compose on stdin."""
        fold_docs, fates, dropped = {}, {}, {}
        for name, c in classified.items():
            routes_doc = docs[REVIEWED[name][1]]
            at = sum(1 for ref in c["side"] if self._side_pos(texts[name], name, ref) < routes_index(texts[name], name, str(self.R)))
            doc, f = build_fold(name, c["side"], at, docs, routes_doc, decisions, gateway)
            fold_docs[name] = doc
            for ref, ident, fate, detail in f:
                fates.setdefault(ref, []).append((ref, ident, fate, detail))
            dropped[name] = sorted({ident for ref, ident, fate, _ in f if fate == "dropped"})
        image_free = {GATEWAY_SERVICE} if gateway == "base" else set()
        before, after, proofs = {}, {}, {}
        for name in WRAPPERS:
            before[name] = self.render_wrapper(name)
            candidate = new_wrapper_text(texts[name], name, str(self.R), STDIN)
            after[name] = self.render(candidate, dump_layer(fold_docs[name]))
            proofs[name] = check_equivalent(before[name], after[name], beta[name], image_free, dropped[name])
        proofs["running_not_as_compose_after_fold"] = self.running_vs_compose(after)
        return proofs, fold_docs, fates, dropped, before

    def _side_pos(self, text: str, wrapper: str, ref: str) -> int:
        refs = [r for r in (operator_ref(p, str(self.R)) for p in wrapper_files(text)) if r]
        return refs.index(ref)

    # rollback
    def _restore(self, state: dict, before: dict) -> None:
        for path, meta in state["files"].items():
            target = pathlib.Path(path)
            data = pathlib.Path(meta["backup"]).read_bytes()
            tmp = target.with_name(target.name + ".fold-tmp")
            tmp.write_bytes(data)
            os.chmod(tmp, meta["mode"])
            try:
                os.chown(tmp, meta["uid"], meta["gid"])
            except PermissionError:                 # pragma: no cover
                pass
            os.replace(tmp, target)
        for path in state["created"]:
            pathlib.Path(path).unlink(missing_ok=True)
        for path, meta in state["files"].items():
            assert hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest() == meta["sha256"], "restored file differs: " + pathlib.Path(path).name
        for name in WRAPPERS:
            now = self.render_wrapper(name)
            bad = differing_fields(now, before[name])
            if bad:
                raise RuntimeError(f"restored {WRAPPERS[name]} renders differently, at: " + ", ".join(bad[:8]))

    def rollback(self) -> dict:
        self.check_quiet()
        state_path = self.FOLD / "state.private.json"
        if not state_path.exists():
            raise Refused(f"no fold in {self.FOLD.name}: nothing to roll back")
        state = json.loads(state_path.read_text())
        if state.get("status") != "folded":
            raise Refused(f"{self.FOLD.name} is {state.get('status')}, not folded: nothing to roll back")
        for path, want in state["new_sha256"].items():
            if hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest() != want:
                raise Refused(f"{pathlib.Path(path).name} changed since the fold; restoring it would lose that. Nothing changed.")
        backup = pathlib.Path(state["backup"])
        before = {name: json.loads((backup / f"render.{name}.private.json").read_text()) for name in WRAPPERS}
        self._restore(state, before)
        state["status"] = "rolled_back"
        state["rolled_back_at"] = self.clock()
        self.private(state_path, json.dumps(state, indent=1))
        (self.FOLD / "DROPPED.md").unlink(missing_ok=True)
        self.record("rolled_back", {"files": sorted(state["files"])})
        self.say("RESULT rolled back: both wrappers are byte for byte the backed-up ones and render as before")
        return {"status": "rolled_back"}


# ── The Mac side: one script for ssm_run.sh ──

def host_script(action_args: list, source=None) -> str:
    """A bash script that lands this exact file in a private temp file on Dev, checks its hash and runs one action."""
    data = pathlib.Path(source or __file__).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    blob = base64.b64encode(gzip.compress(data, mtime=0)).decode()
    for arg in action_args:
        if not re.fullmatch(r"[A-Za-z0-9_=./-]+", arg):
            raise Refused("an argument holds a character the script would have to quote")
    args = " ".join(action_args)
    return f"""set -eu
[ -d {STAGING_ROOT} ] || {{ echo 'not the Dev host'; exit 1; }}
F=$(mktemp /tmp/dev_layer_fold.XXXXXX)
trap 'rm -f "$F"' EXIT
echo '{blob}' | base64 -d | gunzip > "$F"
echo '{digest}  '"$F" | sha256sum -c --quiet
umask 077
python3 "$F" {args}
"""


def main(argv=None):                                # pragma: no cover
    import fcntl
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("plan", "fold", "rollback"):
        p = sub.add_parser(name)
        p.add_argument("--root", default=STAGING_ROOT)
        p.add_argument("--date", default=None, help="YYYYMMDD of operators/dev-fold-<date>; default today, UTC")
        if name == "plan":
            p.add_argument("--prove", choices=("keep", "base"), default=None,
                           help="also render the candidate with docker compose config (the fold on stdin; still read-only), with this gateway pick")
        if name in ("plan", "fold"):
            p.add_argument("--depth-proof", choices=("on", "off"), default=DEFAULT_DECISIONS["depth_proof"])
            p.add_argument("--metered", choices=("depth-v7", "routes"), default=DEFAULT_DECISIONS["metered"])
        if name == "fold":
            p.add_argument("--execute", action="store_true")
            p.add_argument("--gateway-image", choices=("keep", "base"), default=None)
    p = sub.add_parser("host-script")
    p.add_argument("--out", required=True)
    p.add_argument("rest", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.action == "host-script":
        rest = [a for a in args.rest if a != "--"]
        if not rest or rest[0] not in ("plan", "fold", "rollback"):
            parser.error("host-script --out FILE -- plan|fold|rollback [flags]")
        out = pathlib.Path(args.out)
        out.write_text(host_script(rest))
        os.chmod(out, 0o600)
        print(f"wrote {out}: run it with ssm_run.sh (Dev, {STAGING_INSTANCE})")
        return
    if yaml is None:
        raise SystemExit("PyYAML is needed")
    host = Host(root=args.root, date=args.date)
    decisions = {"depth_proof": getattr(args, "depth_proof", "on"), "metered": getattr(args, "metered", "depth-v7")}
    try:
        if args.action == "plan":
            plan = host.plan(decisions, args.prove)
            check_plan(plan)
            print("RESULT planned; nothing changed")
            return
        if args.action == "fold" and not args.execute:
            raise Refused("fold changes the wrappers: pass --execute (and run plan first)")
        with (host.R / LOCK).open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused("another Dev deploy holds the lock; nothing changed")
            if args.action == "fold":
                host.fold(decisions, args.gateway_image)
            else:
                host.rollback()
    except Refused as exc:
        print(f"REFUSED {exc}", flush=True)
        raise SystemExit(2)
    except BaseException as exc:
        print(f"FAILED {type(exc).__name__}: {str(exc)[:400]}", flush=True)     # messages carry names only
        raise SystemExit(1)


if __name__ == "__main__":                          # pragma: no cover
    main()
