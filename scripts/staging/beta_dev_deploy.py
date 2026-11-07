#!/usr/bin/env python3
"""Dev only: MiniMoi's combined 1.2 candidate as thin overlay images on what Dev runs now. ``dry-run`` changes nothing live: it
builds every overlay and proves it. ``apply`` and ``rollback`` run detached, under the deploy lock, through guarded_swap: workers
resume only after a verified result; the running digest, the reviewed payload and the restored config are each proved.

Each overlay is ``FROM <exact running image id>`` plus the changed files, tagged RELEASE. The tags sit in overrides this operator
owns, layered last, so no existing override changes. Plan routes, catalogue, default A, COS_BACKGROUND_JOBS=0,
ROOMS_RECORDS_PROFILE=dev and every *_ENV value stay as they are. Never touches production, sign-ins, authority files or gates."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

RELEASE = "beta-20261107a"
STAGING_ROOT = "/opt/minimoi-staging"
OPERATOR_NAME = "beta-dev-20261106"
MIN_FREE_BYTES = 2 * 1024 ** 3
SAMPLE_PER_IMAGE = 12
WORKER_STOP_SECONDS = 120
MC_WAIT, AGENT_WAIT, DEFAULT_WAIT = 720, 660, 180    # MC: gateway owner lease plus a 3-minute healthcheck, seen above 6 minutes
A = "plan-openai-strong"
AGENT_ENV = ("COS_RUNTIME_MODEL_ACCESS", "COS_PLAN_MODEL", "COS_PLAN_ROUTES", "COS_PLAN_AUTH_PROFILE")
REQUIRED_ENV = {"minimoi-cos-scheduler": {"COS_BACKGROUND_JOBS": "0"}, "minimoi-guildhall-cos": {"ROOMS_RECORDS_PROFILE": "dev"},
                "minimoi-guildhall-mc": {"ROOMS_RECORDS_PROFILE": "dev"}}
PORTAL_ROUTES = ("/app/cos/task-proposals", "/app/cos/work-cards")
MC_TOOLS_PORT = 5011

# The image each kind of file came from: the running tag's own commit. The candidate is ``git diff`` from there.
# Every image diffs from the oldest commit any of them could hold, so Dev runs the release's files even where a tag's image
# held older copies than its name says (seen on the scheduler, portal, German and Portuguese, 6 Nov).
BASES = {"portal": "8481940d", "cos": "8481940d", "domains": "8481940d", "records": "fd6ca08a", "board": "29d7d59a"}
OLDEST_ACCEPTED = "8481940d"                         # A live file may be any committed version since here: never a hand edit.

MC_DIR, COS_DIR, PLUGINS = "/opt/minimoi/mc-agent/", "/opt/minimoi/cos-agent-a/", "/opt/minimoi/openclaw-plugins/"
HANDBOOK, PERSONA = "services/mc_operations/handbook/", "services/mc_operations/personas/mc-chat/releases/0.1.26/"
VOICE_JS = "core/realtime_voice/static/realtime-voice-controller.js"
TEXT = (".py", ".js", ".mjs", ".cjs", ".json", ".html", ".css")


def _app(*paths):
    return tuple((p, "/app/" + p) for p in paths)


# The domain images hold the whole app: their overlays carry the shared code too, or Dev runs an older copy than the commit (a
# Deeper Dive on 7 Nov 2026 failed on a stale domains/guild/services/challenger.py).
_SHARED = ("core/", "domains/guild/", "contracts/cos/", "contracts/partnership/")
_SCHEDULER = _app("core/", "domains/cos/", "domains/guild/", "contracts/partnership/", "services/partnership_tasks/")
_SCHEDULER_LAYOUT = ("core/", "domains/cos/", "domains/guild/", "contracts/partnership/", "services/partnership_tasks/")
_TASKS = _app("contracts/partnership/", "services/partnership_tasks/")     # the scheduler's image, running only the task service's modules
_TASKS_LAYOUT = ("contracts/partnership/", "services/partnership_tasks/")
_BOTS_ONLY = ("core/telegram/telegram_bot.py",)      # needs domains/ an image without it never runs; the curator and the system bot run it


def _python(*modules):
    return ("python", "-c", "import " + ",".join(modules))


# tier: lowest risk first. MC is last. A worker is created in place and started only after verification. rules: what the image's
# Dockerfile lays out for files this release changed, (repo path or prefix, path in the image). No rule match: not in this image.
TARGETS = {
    "model-gateway": dict(container="minimoi-model-gateway", tier=1, base="portal", kind="config", wait=DEFAULT_WAIT, config="litellm.staging.yaml",
                          rules=(("services/model_gateway/litellm.staging.yaml", "@config/litellm.staging.yaml"),),
                          health="http://127.0.0.1:4000/health/liveliness", down="about 1 minute (config reread on recreate)"),
    "curator": dict(container="minimoi-curator", repo="curator", tier=1, base="domains", old="minimoi12-8481940d", wait=DEFAULT_WAIT,
                    rules=_app("domains/curator/", *_SHARED), layout=("domains/curator/", *_SHARED),
                    health="http://127.0.0.1:8766/health", down="under 1 minute"),
    "german": dict(container="minimoi-german", repo="german", tier=1, base="domains", old="minimoi12-8481940d", wait=DEFAULT_WAIT,
                   rules=_app("domains/german/", *_SHARED), layout=("domains/german/", *_SHARED),
                   smoke=("python", "-c", "import sys;sys.path.insert(0,'/app/domains/german');sys.path.insert(0,'/app');import german_domain as g;assert g.grok_text_on() is False"),
                   health="http://127.0.0.1:8767/health", down="under 1 minute"),
    "portuguese": dict(container="minimoi-portuguese", repo="portuguese", tier=1, base="domains", old="minimoi12-8481940d", wait=DEFAULT_WAIT,
                       rules=_app("domains/portuguese/", *_SHARED), layout=("domains/portuguese/", *_SHARED),
                       health="http://127.0.0.1:8770/health", down="under 1 minute"),
    "partnership-tasks": dict(container="minimoi-partnership-tasks", repo="partnership-tasks", tier=1, base="cos", wait=DEFAULT_WAIT, rules=_TASKS,
                              layout=_TASKS_LAYOUT, smoke=_python("services.partnership_tasks.http_api"),
                              health="http://127.0.0.1:8772/healthz", down="under 1 minute"),
    "cos-scheduler": dict(container="minimoi-cos-scheduler", repo="cos-scheduler", tier=2, base="cos", old="plan-20261105", wait=DEFAULT_WAIT,
                          image_env=(("MINIMOI_COS_TO_MC", "1"), ("COS_TO_MC_URL", "http://minimoi-portal:5011/internal/mc/v1/from-cos")), rules=_SCHEDULER, exclude=_BOTS_ONLY,
                          layout=_SCHEDULER_LAYOUT, smoke=_python("core.model_routes", "domains.cos.model_route"),
                          health="http://127.0.0.1:8769/health", down="under 2 minutes; CoS refuses messages meanwhile"),
    "cos-agent-a": dict(container="minimoi-cos-agent-a", repo="cos-agent-a", tier=3, base="cos", old="plan-20261105d", wait=AGENT_WAIT, kind="node", build="cos",
                        rules=(("docker/cos-agent-a/apply-config.sh", COS_DIR + "apply-config.sh"), ("docker/cos-agent-a/plan-access.cjs", COS_DIR + "plan-access.cjs"),
                               ("docker/cos-agent-a/build-mc-handbook.mjs", COS_DIR + "build-mc-handbook.mjs"),
                               ("docker/cos-agent-a/openclaw.json", COS_DIR + "openclaw.json"), ("docker/cos-agent-a/COS_AGENTS.md", COS_DIR + "COS_AGENTS.md"),
                               ("docker/cos-agent-a/plugins/cos-bound/", PLUGINS + "cos-bound/"), (HANDBOOK, COS_DIR + "handbook/")),
                        always=(("docker/cos-agent-a/AGENTS.md", COS_DIR + "AGENTS.md"),), executables=(COS_DIR + "apply-config.sh",),
                        down="up to 11 minutes (gateway owner lease); CoS refuses messages meanwhile"),
    "portal": dict(container="minimoi-portal", repo="portal", tier=4, base="portal", old="route-roles-20261105g", wait=DEFAULT_WAIT,
                   image_env=(("MINIMOI_COS_TO_MC", "1"),),
                   rules=_app("core/", "domains/guild/", "minimoi_portal/", "contracts/partnership/", "services/partnership_tasks/", "services/rooms_worker/",
                              "services/mc_operations/"),
                   exclude=_BOTS_ONLY, layout=("core/", "minimoi_portal/", "services/partnership_tasks/", "services/rooms_worker/"),
                   smoke=("python", "-c", "from core.mc_tools.service import ASK_FOLLOW_UP;import core.model_routes as m;import minimoi_portal.guild_ui.api;m.relay_routes(m.load_catalogue())"),
                   health="http://127.0.0.1:5001/health", down="under 2 minutes; pages fail meanwhile"),
    "mc-relay": dict(container="minimoi-mc-relay", repo="mc-relay", tier=5, base="portal", old="route-roles-20261105e", wait=DEFAULT_WAIT, kind="node",
                     rules=(("docker/mc-agent/relay_v2.mjs", MC_DIR + "relay_v2.mjs"),), down="under 1 minute, inside MC's window"),
    "mc-agent": dict(container="minimoi-mc-agent", repo="mc-agent", tier=6, base="portal", old="route-roles-20261105g", wait=MC_WAIT, kind="node", build="mc",
                     rules=(("docker/mc-agent-container/start.sh", MC_DIR + "start.sh"), ("docker/mc-agent-container/model-access.cjs", MC_DIR + "model-access.cjs"),
                            ("docker/mc-agent-container/handbook.mjs", MC_DIR + "handbook.mjs"), ("docker/mc-agent/relay_v2.mjs", MC_DIR + "relay_v2.mjs"),
                            ("services/mc_operations/plugins/minimoi-tools/", MC_DIR + "plugins/minimoi-tools/"),
                            (PERSONA, MC_DIR + "persona/"), (HANDBOOK + "manifest.json", MC_DIR + "handbook/manifest.json"),
                            (HANDBOOK + "v0.1.2/", MC_DIR + "handbook/v0.1.2/"),
                            ("scripts/ops/mc_container_security_test.py", "/opt/minimoi/mc-release/mc_container_security_test.py")),
                     replaces=(MC_DIR + "persona/", MC_DIR + "handbook/"), executables=(MC_DIR + "start.sh",), down="6 to 12 minutes (owner lease plus a 3-minute healthcheck)"),
    "guildhall-cos": dict(container="minimoi-guildhall-cos", repo="guildhall-cos", tier=7, base="portal", worker=True, wait=DEFAULT_WAIT,
                          rules=_app("services/rooms_worker/"), layout=("services/rooms_worker/",), smoke=_python("services.rooms_worker.cos"),
                          down="the whole swap, plus its restart: meetings wait"),
    "guildhall-mc": dict(container="minimoi-guildhall-mc", repo="guildhall-mc", tier=7, base="portal", worker=True, wait=DEFAULT_WAIT,
                         rules=_app("services/rooms_worker/"), layout=("services/rooms_worker/",), smoke=_python("services.rooms_worker.worker"),
                         down="the whole swap, plus its restart: meetings wait"),
    "partnership-worker": dict(container="minimoi-partnership-worker", repo="partnership-worker", tier=7, base="cos", worker=True, wait=DEFAULT_WAIT, rules=_TASKS,
                               layout=_TASKS_LAYOUT, smoke=_python("services.partnership_tasks.worker"), down="the whole swap, plus its restart: tasks wait"),
    # Nothing these run changed in this release. A rule match later would put them in the plan.
    "records": dict(container="minimoi-records", repo="records", tier=7, base="records", worker=True, down="the whole swap, plus its restart: meetings wait", rules=_app("domains/records/"),
                    extra_rules=(("prototype-lab/projects/project-records-room-poc/", "/app/"),), wait=DEFAULT_WAIT),
    "cos-board": dict(container="minimoi-cos-board", repo="cos-board", tier=7, base="board", worker=True, wait=DEFAULT_WAIT, down="the whole swap, plus its restart: CoS Board notes wait",
                      rules=_app("core/domain_api/", "core/domain_operations/", "domains/guild/board", "domains/guild/queue_store.py", "minimoi_portal/guild_ui/adapters/",
                                 "minimoi_portal/guild_ui/board", "minimoi_portal/guild_ui/stores.py", "utils/payment_scrub.py")),
    "supervisor": dict(container="minimoi-supervisor", repo="supervisor", tier=1, base="portal", wait=DEFAULT_WAIT,
                       rules=_app("services/supervisor/", "core/activity/", "core/supervision/", "core/canonical_json.py", "core/durable_files.py",
                                  "core/mc_delegation_dispatcher/snapshot.py")),
}
WORKERS = tuple(t["container"] for t in TARGETS.values() if t.get("worker"))


# Host config files no commit produced, reviewed by hand and accepted by exact hash. Dev's gateway file is a JSON render from
# 1 Nov: compared route by route with the candidate, it differs only in the voice roles' info, the added local route, and
# minimoi-cos-agent-xai-fast, whose Grok fallback the candidate replaces. Any other host file is still refused.
REVIEWED_HOST_CONFIGS = frozenset({"a50a08e429c7f6a1237089a588e2985a8dcb972eec1ad8c582bc3672741ee666"})


class Refused(RuntimeError):
    """Stopped before anything live changed."""


# ── Pure checks, tested without a host ──

def refuse_production(path) -> None:
    """Production's root is /opt/minimoi. /opt/minimoi-staging is not inside it, though it starts with the same letters."""
    norm = os.path.normpath(str(path))
    if norm == "/opt/minimoi" or norm.startswith("/opt/minimoi/"):
        raise Refused("production path refused: " + norm)


def refuse_production_text(text: str, where: str) -> None:
    """A wrapper or render that names a host path under production's root. The image's own /opt/minimoi/mc-agent is not a host path, so callers pass host-side text only."""
    if re.search(r"/opt/minimoi(?![-\w])", text):
        raise Refused("production path named in " + where)


def refuse_production_sources(render: dict, services) -> None:
    """No bind mount or env file of a service this deploy recreates may come from production's root."""
    for name in services:
        svc = render["services"][name]
        for v in svc.get("volumes") or []:
            refuse_production(v.get("source", "") if isinstance(v, dict) else str(v).split(":")[0])
        for f in svc.get("env_file") or []:
            refuse_production(f if isinstance(f, str) else f.get("path", ""))


def check_free(free_bytes: int, where: str) -> None:
    if free_bytes < MIN_FREE_BYTES:
        raise Refused(f"only {free_bytes / 1024 ** 3:.2f} GB free on {where}; 2 GB is the floor. Nothing changed.")


def dest_for(rules, path: str):
    """Where this image holds a repo path, or None when it does not."""
    for src, dest in rules:
        if path == src:
            return dest
        is_file = pathlib.PurePosixPath(src).suffix and not src.endswith("/")
        if not is_file and path.startswith(src):    # a folder, or a name prefix such as domains/guild/board
            return dest + path[len(src):]
    return None


def files_for(spec: dict, names) -> dict:
    """{repo path: path in the image} for a target and the paths this release changed. A file absent from the image is never a guess: no rule, not here."""
    rules = (*spec["rules"], *spec.get("extra_rules", ()))
    out = {n: d for n in names if n not in spec.get("exclude", ()) for d in [dest_for(rules, n)] if d}
    if out:
        out.update(dict(spec.get("always", ())))    # an unchanged input a RUN step needs: only when there is a build at all
    return out


def want_hashes(files: dict) -> dict:
    """{image path: sha256} a built image must hold. An input a RUN step compiles over is not itself in the result."""
    return {m["dest"]: m["after"] for m in files.values() if not m.get("generated_input")}


def dockerfile_for(spec: dict, base_id: str, user: str) -> str:
    """FROM the exact running image id, never a tag. RUN steps restore the image's own user, so the runtime contract does not move."""
    assert base_id.startswith("sha256:"), "an overlay builds on an image id, not a tag"
    lines, build = ["FROM " + base_id], spec.get("build")
    if build and user:
        lines.append("USER root")
    if build == "mc":
        lines.append(f"RUN rm -rf {MC_DIR}persona {MC_DIR}handbook")
    lines.append("COPY ctx/ /")
    # Compose stays image-only (only_images); a rollback to the old image takes the switch with it.
    lines += [f"ENV {name}={value}" for name, value in spec.get("image_env", ())]
    if build == "mc":
        lines.append(f"RUN chmod 0755 {MC_DIR}start.sh && sh {MC_DIR}.beta-check-6c.sh && rm -f {MC_DIR}.beta-check-6c.sh")
        lines.append("LABEL minimoi.persona=0.1.26 minimoi.handbook=0.1.2")
    if build == "cos":
        lines.append(f"RUN node {COS_DIR}build-mc-role.mjs {COS_DIR}AGENTS.md {COS_DIR}owner-role {COS_DIR}mc-role-lock.json {COS_DIR}AGENTS.md {COS_DIR}ROLE_LOAD.json")
        lines.append(f"RUN node {COS_DIR}build-mc-handbook.mjs {COS_DIR}AGENTS.md {COS_DIR}handbook {COS_DIR}AGENTS.md {COS_DIR}ROLE_LOAD.json")
    if build and user:
        lines.append("USER " + user)
    return "\n".join(lines) + "\n"


# Run inside the MC overlay's build. Same steps as start.sh's handbook load, then check 6c itself: start.sh must refuse a handbook
# that differs from the persona's pin. A bad handbook fails here, before anything is swapped.
CHECK_6C = r"""#!/bin/sh
set -eu
D="${BETA_MC_DIR:-/opt/minimoi/mc-agent}"
S=$(mktemp -d)
hb=$(node -e '
const h=require(process.argv[1]).handbook||{};
if (!/^[0-9]+\.[0-9]+\.[0-9]+$/.test(h.version||"")||!/^[0-9a-f]{64}$/.test(h.manifest_sha256||"")) process.exit(1);
console.log(h.version+" "+h.manifest_sha256);
' "$D/persona/manifest.json")
# shellcheck disable=SC2086
block=$(node "$D/handbook.mjs" "$D/handbook" "$S/ws" $hb)
{ cat "$D/persona/AGENTS.md"; printf '\n'; cat "$D/persona/RUNTIME.openclaw.md"; printf '\n%s\n' "$block"; } > "$S/AGENTS.md"
chars=$(node -e 'console.log([...require("fs").readFileSync(process.argv[1],"utf8")].length)' "$S/AGENTS.md")
[ "$chars" -le 16000 ] || { echo "projected AGENTS.md is $chars characters (budget 16000)" >&2; exit 1; }
cp "$D/handbook/manifest.json" "$S/manifest.good"
printf '{"format": "minimoi-mc-handbook/1", "files": {}}\n' > "$D/handbook/manifest.json"
limit=""; command -v timeout >/dev/null 2>&1 && limit="timeout 60"
set +e
out=$(OPENCLAW_STATE_DIR="$S/state" OPENCLAW_GATEWAY_TOKEN=t $limit sh "$D/start.sh" 2>&1 </dev/null); code=$?
set -e
cp "$S/manifest.good" "$D/handbook/manifest.json"
[ "$code" = 78 ] || { echo "check 6c: start exited $code, want 78" >&2; exit 1; }
printf '%s\n' "$out" | grep -Eq 'REFUSED: handbook [0-9.]+ failed verification' || { echo "check 6c: no refusal line" >&2; exit 1; }
printf '%s\n' "$out" | grep -q 'the persona pins' || { echo "check 6c: refusal does not name the pin" >&2; exit 1; }
rm -rf "$S"
echo "check 6c passed; projection $chars characters"
"""

# Run inside a built Python image: every project import in a payload file must resolve there. Reads files, executes none of them,
# so a file ahead of its image's other files is found before the swap. Imports under ``try`` are optional and pass.
IMPORT_CHECK = r'''
import ast, json, os, sys
root, files = sys.argv[1], sys.argv[2:]
local = {d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))}
def find(mod):
    p = os.path.join(root, *mod.split("."))
    if os.path.isfile(p + ".py"): return p + ".py"
    if os.path.isfile(os.path.join(p, "__init__.py")): return os.path.join(p, "__init__.py")
    return p if os.path.isdir(p) else None
def names(path):
    out, star = set(), [False]
    def walk(body):
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): out.add(n.name)
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    out.update(x.id for x in ast.walk(t) if isinstance(x, ast.Name))
            elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
                if isinstance(n.target, ast.Name): out.add(n.target.id)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    if a.name == "*": star[0] = True
                    else: out.add((a.asname or a.name).split(".")[0])
            else:
                for field in ("body", "orelse", "finalbody"): walk(getattr(n, field, None) or [])
                for h in getattr(n, "handlers", None) or []: walk(h.body)
    walk(ast.parse(open(path, encoding="utf-8").read()).body)
    return None if star[0] else out
bad = set()
def check_from(base, wanted, where):
    if not base or base.split(".")[0] not in local: return
    found = find(base)
    if found is None:
        bad.add(where + ": " + base); return
    known = names(found) if found.endswith(".py") else set()
    for name in wanted:
        if name != "*" and find(base + "." + name) is None and (known is not None and name not in known):
            bad.add(where + ": " + base + "." + name)
def scan(node, parts, where):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.Try):
            for h in child.handlers: scan(h, parts, where)
            for extra in child.orelse + child.finalbody: scan(extra, parts, where)
            continue
        if isinstance(child, ast.Import):
            for a in child.names:
                if a.name.split(".")[0] in local and find(a.name) is None: bad.add(where + ": " + a.name)
        elif isinstance(child, ast.ImportFrom):
            base = child.module or ""
            if child.level:
                up = parts[:len(parts) - child.level]
                base = ".".join([*up, base]) if base else ".".join(up)
            check_from(base, [a.name for a in child.names], where)
        else:
            scan(child, parts, where)
for f in files:
    if f.endswith(".py"):
        rel = os.path.relpath(f, root)
        scan(ast.parse(open(f, encoding="utf-8").read()), tuple(rel[:-3].split(os.sep)), rel)
print(json.dumps(sorted(bad)))
'''

# Run inside a built Python image: which reviewed files, beyond this payload, differ from the commit. Read on stdin: {image path: sha256}.
LAYOUT_CHECK = r'''
import hashlib, json, sys
differs, missing = [], []
for path, want in json.load(sys.stdin).items():
    try:
        got = hashlib.sha256(open(path, "rb").read()).hexdigest()
    except OSError:
        missing.append(path); continue
    if got != want: differs.append(path)
print(json.dumps({"differs": sorted(differs), "missing": sorted(missing)}))
'''


def check_payload(meta_files: dict, read) -> None:
    """Every file about to ship is the reviewed one, byte for byte. ``read(repo path) -> bytes``."""
    assert meta_files, "empty payload"
    for name, m in meta_files.items():
        assert hashlib.sha256(read(name)).hexdigest() == m["after"], "payload differs from what was reviewed: " + name


def check_release(tar_bytes: bytes, recorded_sha: str) -> None:
    """The tarball on the host is the one built from the exact commit on the Mac."""
    got = hashlib.sha256(tar_bytes).hexdigest()
    assert recorded_sha and got == recorded_sha, "release tarball differs from its recorded sha256"


def base_mismatches(files: dict, live: dict, replaces=()) -> list:
    """Which payload paths would overwrite something no commit explains. files: {repo path: meta}. live: {image path: sha256}."""
    bad = []
    for name, m in files.items():
        dest = m["dest"]
        if any(dest.startswith(r) for r in replaces) or m.get("generated_input"):
            continue
        got = live.get(dest)
        if got is not None and got not in m["accepted"] and got != m["after"]:      # absent: this image simply gains the file
            bad.append(dest + ": no commit explains the running file")
    return bad


def not_on_declared_base(files: dict, live: dict, replaces=()) -> list:
    """Payload paths whose running file is a committed version other than the one the image's tag implies. Allowed, and said aloud."""
    return sorted(m["dest"] for m in files.values() if not m.get("generated_input") and not any(m["dest"].startswith(r) for r in replaces)
                  and live.get(m["dest"]) not in (None, m.get("base"), m["after"]))


def check_tag_is_running(tag: str, tag_id: str, running_id: str) -> None:
    """A tag can move. The image this deploy builds on must be the very image running now."""
    assert tag_id and tag_id == running_id, "tag no longer names the running image: " + tag


def check_overlay(base: dict, new: dict, allowed=("Image", "Labels"), image_env=()) -> None:
    """A child of the running image, with the same runtime config, plus exactly the switches its target names."""
    b = base["RootFS"]["Layers"]
    assert new["RootFS"]["Layers"][:len(b)] == b, "image is not a child of the running one"
    strip = lambda c: {k: v for k, v in c.items() if k not in allowed}
    want = dict(base["Config"])
    if image_env:
        want["Env"] = [*(base["Config"].get("Env") or []), *(f"{name}={value}" for name, value in image_env)]
    assert strip(new["Config"]) == strip(want), "the overlay's image config changed"


def only_images(before: dict, after: dict, images: dict) -> None:
    """What compose would run differs by these services' images and pull policy, and by nothing else on any service."""
    b, a = copy.deepcopy(before.get("services", {})), copy.deepcopy(after.get("services", {}))
    assert set(a) == set(b), "the services would change"
    for service, image in images.items():
        assert a[service].pop("image") == image, "image: " + service
        assert a[service].pop("pull_policy", None) == "never", "pull policy: " + service
        b[service].pop("image")
        b[service].pop("pull_policy", None)
    for name in a:
        assert a[name] == b[name], "changed beyond its image: " + name
    strip = lambda d: {k: v for k, v in d.items() if k != "services"}
    assert strip(after) == strip(before), "changed beyond its services"


def override_doc(images: dict) -> dict:
    return {"services": {service: {"image": image, "pull_policy": "never"} for service, image in sorted(images.items())}}


def with_override(body: str, override: str) -> str:
    """The wrapper with one more -f, last, so it wins and nothing above it changes."""
    assert body.count('"$@"') == 1, "wrapper shape"
    return body.replace('"$@"', f'-f {override} "$@"')


def without_override(body: str, override: str) -> str:
    assert body.count(f'-f {override} "$@"') == 1, "the override is not in the wrapper"
    return body.replace(f'-f {override} "$@"', '"$@"')


def dev_values(env) -> dict:
    """The settings this deploy must leave alone. env: KEY=VALUE strings."""
    pairs = dict(e.split("=", 1) for e in env)
    return {k: v for k, v in pairs.items() if k.endswith("_ENV") or k in ("COS_BACKGROUND_JOBS", "ROOMS_RECORDS_PROFILE", "MINIMOI_MODEL_ROUTES_FILE", "MINIMOI_ROLE")}


def check_required_env(container: str, env) -> None:
    pairs = dict(e.split("=", 1) for e in env)
    for key, want in REQUIRED_ENV.get(container, {}).items():
        assert pairs.get(key) == want, f"{container}: {key} is not the Dev value"


def check_catalogue(cat: dict) -> None:
    """Dev's host catalogue keeps default A for both conversations."""
    for role in ("cos-conversation", "mc-conversation"):
        assert cat["roles"][role]["route"] == A, "default route moved: " + role


def shadowed(dest: str, mounts) -> bool:
    """A mount hides this path in the running container, so it cannot be read back there. The image was checked before."""
    return any(dest == m or dest.startswith(m.rstrip("/") + "/") for m in mounts)


def sample(dests, mounts=(), limit=SAMPLE_PER_IMAGE) -> list:
    """Evenly spaced across the sorted payload, fixed for a given payload, so a re-check reads the same files."""
    open_paths = sorted(d for d in dests if not shadowed(d, mounts))
    if len(open_paths) <= limit:
        return open_paths
    return [open_paths[i * len(open_paths) // limit] for i in range(limit)]


def parse_sums(stdout: str) -> dict:
    """sha256sum's output; a file it could not read is simply absent."""
    out = {}
    for line in stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            out[parts[1].lstrip("*")] = parts[0]
    return out


def check_files(want: dict, got: dict, where: str) -> None:
    """want, got: {image path: sha256}."""
    for dest, h in want.items():
        assert got.get(dest) == h, f"{where} holds a different file: {dest}"


def primary_network(mode, networks):
    """A container's first network by name. Docker records NetworkMode as a name, or as the network's id for a container
    created by id (the CoS Board on Dev); "default" means its only one. Anything else is refused, not guessed."""
    if mode in networks:
        return mode
    by_id = [name for name, net in networks.items() if net.get("NetworkID") == mode]
    if len(by_id) == 1:
        return by_id[0]
    if mode == "default" and len(networks) == 1:
        return next(iter(networks))
    raise Refused("cannot tell the first network of a standalone container: " + str(mode)[:20])


def _same_meaning(contract):
    """Docker writes OomKillDisable as false on a new container where an older one holds null. Both mean off (seen on Dev,
    6 Nov: it stopped an apply and its restore). Nothing else is forgiven."""
    value = json.loads(json.dumps(contract))
    host = value.get("host") if isinstance(value, dict) else None
    if isinstance(host, dict) and not host.get("OomKillDisable"):
        host["OomKillDisable"] = False
    return value


def _differing_fields(left, right, path=""):
    """Which fields differ, by name only: a value here could be a secret."""
    if isinstance(left, dict) and isinstance(right, dict):
        return [f for key in sorted(set(left) | set(right)) for f in _differing_fields(left.get(key), right.get(key), f"{path}/{key}")]
    return [] if left == right else [path or "/"]


def with_image_env(saved: dict, spec: dict) -> dict:
    """What a new container should hold: the saved contract plus exactly its target's image switches. A restore compares with the saved one alone."""
    added = [f"{name}={value}" for name, value in spec.get("image_env", ())]
    return {**saved, "env": sorted([*saved["env"], *added])}


def sorted_env(contract: dict) -> dict:
    return {**contract, "env": sorted(contract["env"])}


def check_restored(saved: dict, live: dict, what: str) -> None:
    """After a restore, what was saved is what is live, whole."""
    left, right = _same_meaning(saved), _same_meaning(live)
    assert left == right, "restored " + what + " differs from the saved one, at: " + ", ".join(_differing_fields(left, right)[:8])


def same_runtime(saved: dict, live: dict) -> None:
    """After a restore MC's runtime config is the saved one. Only the runtime's own write stamp may differ."""
    strip = lambda c: {k: v for k, v in c.items() if k != "meta"}
    assert strip(saved) == strip(live), "restored runtime config differs from the saved one"


def check_mc_policy(live: dict) -> None:
    """The runtime policy this deploy must leave as reviewed: conversation limits, no API route."""
    chat = live["agents"]["entries"]["mc-chat"]["tools"]
    assert "group:runtime" in chat["deny"] and chat["fs"]["workspaceOnly"] is True, "conversation limits"
    assert "group:runtime" not in live["agents"]["entries"]["mc-agent"]["tools"]["deny"], "job agent"
    assert all(p.get("mode") == "oauth" for p in live["auth"]["profiles"].values()), "an API profile appeared"
    assert not (live.get("models") or {}).get("providers"), "an API provider appeared"


def write_context(ctx: pathlib.Path, files: dict, payload: pathlib.Path, extra=None) -> None:
    """A build context: ctx/<image path>, modes set from the manifest and no wider. Directories 0755: COPY carries them over the image's own."""
    for name, m in files.items():
        target = ctx / "ctx" / m["dest"].lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(payload / name, target)
        target.chmod(int(m.get("mode", "644"), 8))
    for dest, text in (extra or {}).items():
        target = ctx / "ctx" / dest.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        target.chmod(0o644)
    for here, dirs, _ in os.walk(ctx):
        for d in dirs:
            os.chmod(os.path.join(here, d), 0o755)


# ── The order of things ──

class Deploy:
    """``host`` does the work, so every failure can be tested without a host. Every overlay is built before any container is touched."""

    TIERS = tuple(sorted({t["tier"] for t in TARGETS.values() if not t.get("worker")}))

    def __init__(self, host, guarded_swap):
        self.host, self.guarded_swap = host, guarded_swap

    def _prepare(self):
        h = self.host
        h.check_baseline()
        h.check_disk()
        try:
            h.build_images()
            h.check_new_images()
        finally:
            h.clean_build()                         # a failed build leaves nothing behind either
        h.check_render()
        h.check_disk()

    def dry_run(self) -> dict:
        self._prepare()
        return self.host.plan()

    def apply(self):
        h = self.host
        self._prepare()
        h.save_before()

        def forward(gate):
            h.write_new()
            for tier in self.TIERS:
                h.recreate(tier, gate)
            h.create_workers(gate)                  # created stopped: they start only after this whole swap is verified
            h.verify_new()
        outcome = self._swap(forward, lambda gate: h.restore_verified(gate))
        try:
            h.verify_workers()
        except BaseException as exc:
            h.record("workers_failed_verification", {"exception": type(exc).__name__})
            try:
                self.rollback()
            except BaseException as rollback_exc:
                h.record("rollback_after_worker_failure_failed", {"exception": type(rollback_exc).__name__})
            raise
        h.record("result", {"status": "deployed_verified"})
        return outcome

    def rollback(self):
        h = self.host
        h.check_saved()

        def no_second_try(_):                       # a rollback that failed is not restored: workers must stay stopped
            raise RuntimeError("rollback failed; recovery required")
        outcome = self._swap(lambda gate: h.restore_verified(gate), no_second_try)
        h.verify_restored_workers()
        h.record("result", {"status": "rolled_back_verified"})
        return outcome

    def _swap(self, forward, restore):
        h = self.host
        return self.guarded_swap(stop_workers=h.stop_workers, start_workers=h.start_workers, wait_idle=h.wait_idle, gate=h.gate,
                                 gate_alive=h.gate_alive, apply=forward, restore=restore, record=h.record)


class RealHost:                                     # pragma: no cover  (exercised by the dry-run on Dev)
    def __init__(self, root=STAGING_ROOT, op_module=None):
        self.R = pathlib.Path(root)
        refuse_production(self.R)
        self.OP = self.R / "operators" / OPERATOR_NAME
        refuse_production(self.OP)
        self.REL = self.OP / "release"
        self.meta = json.loads((self.REL / "meta.json").read_text())
        self.targets = {k: v for k, v in self.meta["targets"].items() if v["files"]}
        self.BEFORE = self.OP / "before"
        self.CTX = self.OP / "ctx"
        self.CAT = self.R / "config/model-routes/catalogue.json"
        self.COMPOSE, self.MC_WRAP = self.R / "bin/compose.sh", self.R / "bin/mc.sh"
        self.OVERRIDES = {"compose": self.OP / "compose.beta.yml", "mc": self.OP / "mc.beta.yml"}
        self.WRAPPERS = {"compose": self.COMPOSE, "mc": self.MC_WRAP}
        if op_module is None:
            sys.path.insert(0, str(self.R / "operators/mc-route-roles-20261105"))
            import op as op_module
        self.O = op_module
        self.new_ids, self.where, self.layout_report, self.off_base = {}, {}, {}, {}
        self.others_strict = True

    # plumbing
    def _run(self, *args, input=None, timeout=900, env=None):
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, input=input, env=env)
        if p.returncode:
            log = self.OP / "last-command.private.log"
            log.write_text(" ".join(map(str, args[:6])) + "\n" + p.stdout + "\n" + p.stderr)
            log.chmod(0o600)
            raise RuntimeError(f"command failed (log kept privately): {args[0]} {args[1] if len(args) > 1 else ''}")
        return p.stdout

    def _sealed(self, image, entrypoint, *args, input=None, extra=()):
        return self._run("docker", "run", "--rm", "-i", "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m", "--cap-drop", "ALL",
                         "--security-opt", "no-new-privileges", *extra, "--entrypoint", entrypoint, image, *args, input=input)

    def _image_id(self, ref):
        return json.loads(self._run("docker", "image", "inspect", ref))[0]["Id"]

    def _compose(self, wrapper, *args):
        return self._run(str(self.WRAPPERS[wrapper]), *args)

    def _render(self, wrapper, override=None):
        extra = ("-f", str(override)) if override else ()
        return json.loads(self._compose(wrapper, *extra, "config", "--format", "json"))

    def _hashes_in_image(self, image, paths):
        p = subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "sha256sum", image, *paths], capture_output=True, text=True, timeout=300)
        return parse_sums(p.stdout)                 # rc is 1 when a new file is not there yet: that is a fact, not a failure

    def _hashes_in_container(self, name, paths):
        p = subprocess.run(["docker", "exec", name, "sha256sum", *paths], capture_output=True, text=True, timeout=120)
        return parse_sums(p.stdout)

    def record(self, name, detail):
        with (self.OP / "journal.jsonl").open("a") as f:
            f.write(json.dumps({"at": time.time(), "event": name, **detail}) + "\n")
        if name == "recovery_required":
            (self.OP / "recovery-required.json").write_text(json.dumps({"at": time.time(), **detail}))
        if name in ("result", "outcome"):
            (self.OP / (name + ".json")).write_text(json.dumps({"at": time.time(), **detail}))

    def _targets_by_tier(self, tier):
        return sorted((k for k, v in self.targets.items() if v["tier"] == tier), key=lambda k: (self.targets[k].get("kind") != "config", k))     # the gateway first in its tier

    # baseline
    def _locate(self):
        renders = {w: self._render(w) for w in self.WRAPPERS}
        where = {}
        for key, spec in self.targets.items():
            found = [(w, s) for w, r in renders.items() for s, c in r["services"].items() if c.get("container_name") == spec["container"]]
            assert len(found) <= 1, "two services claim " + spec["container"]
            if found:
                where[key] = found[0]
            else:
                assert spec.get("worker"), spec["container"] + " is in no compose project"     # Guildhall's containers are standalone on Dev
                where[key] = (None, None)
        return renders, where

    def check_baseline(self):
        assert self.R.is_dir() and self.REL.is_dir(), "not the Dev host"
        assert not (self.OP / "recovery-required.json").exists(), "an earlier run needs recovery"
        assert not self.OVERRIDES["compose"].exists() and not self.OVERRIDES["mc"].exists(), "already applied: roll back first"
        check_release((self.OP / "release.tar.gz").read_bytes(), (self.OP / "release.sha256").read_text().split()[0])
        for spec in self.targets.values():
            check_payload(spec["files"], lambda name: (self.REL / "payload" / name).read_bytes())
        for wrapper in self.WRAPPERS.values():
            refuse_production_text(wrapper.read_text(), wrapper.name)
        portal = self.O.inspect("minimoi-portal")
        assert (portal["Config"].get("Labels") or {}).get("com.docker.compose.project") == "minimoi-staging", "the portal is not Dev's"
        assert dict(e.split("=", 1) for e in portal["Config"]["Env"]).get("MINIMOI_ROLE") != "production", "this host says production"
        renders, self.where = self._locate()
        for wrapper, render in renders.items():
            refuse_production_sources(render, [s for w, s in self.where.values() if w == wrapper])
        reviewed = json.loads(self.O.mc_compose("config", "--format", "json"))
        for key, (wrapper, service) in self.where.items():
            if wrapper == "mc":
                assert reviewed["services"][service] == renders["mc"]["services"][service], "bin/mc.sh and the MC operator render differently: " + service
        check_catalogue(json.loads(self.CAT.read_text()))
        problems = []
        self.base, self.off_base = {}, {}
        for key, spec in self.targets.items():
            live = self.O.inspect(spec["container"])
            check_required_env(spec["container"], live["Config"]["Env"])
            if spec.get("kind") == "config":
                got = hashlib.sha256((self.R / "config" / spec["config"]).read_bytes()).hexdigest()
                only = next(iter(spec["files"].values()))
                if got not in only["accepted"] and got != only["after"] and got not in REVIEWED_HOST_CONFIGS:
                    problems.append(f"{spec['container']}: {spec['config']}: no commit explains the host's file")
                continue
            tag = live["Config"]["Image"]
            if spec.get("old"):
                assert tag.endswith(":" + spec["old"]) or tag.startswith("sha256:"), f"unexpected live image on {spec['container']}: {tag}"
            check_tag_is_running(tag, self._image_id(tag), live["Image"])
            wrapper, service = self.where[key]
            if wrapper:
                assert renders[wrapper]["services"][service]["image"] == tag, "compose would not run what is running: " + spec["container"]
            else:
                assert subprocess.run(["docker", "inspect", self._backup(spec)], capture_output=True).returncode != 0, "a backup container is already there: " + spec["container"]
            self.base[key] = {"id": live["Image"], "tag": tag, "user": json.loads(self._run("docker", "image", "inspect", live["Image"]))[0]["Config"].get("User") or ""}
            hashes = self._hashes_in_image(live["Image"], sorted(m["dest"] for m in spec["files"].values()))
            problems += [f"{spec['container']}: {x}" for x in base_mismatches(spec["files"], hashes, spec.get("replaces", ()))]
            off = not_on_declared_base(spec["files"], hashes, spec.get("replaces", ()))
            if off:
                self.off_base[spec["container"]] = off
        if problems:                                # every one at once, so one look finds them all
            (self.OP / "base-mismatches.json").write_text(json.dumps(problems, indent=1))
            for line in problems:
                print("BASE", line, flush=True)
            raise Refused(f"{len(problems)} live file(s) no commit explains; nothing changed. See base-mismatches.json.")

    def check_disk(self):
        root = self._run("docker", "info", "-f", "{{.DockerRootDir}}").strip()
        for where in (root, str(self.OP)):
            check_free(shutil.disk_usage(where).free, where)

    # build
    def build_images(self):
        os.umask(0o022)
        shutil.rmtree(self.CTX, ignore_errors=True)
        for key, spec in self.targets.items():
            if spec.get("kind") == "config":
                continue
            check_payload(spec["files"], lambda name: (self.REL / "payload" / name).read_bytes())    # again, right before it ships
            d = self.CTX / key
            (d / "ctx").mkdir(parents=True)
            write_context(d, spec["files"], self.REL / "payload", {MC_DIR + ".beta-check-6c.sh": CHECK_6C} if spec.get("build") == "mc" else None)
            (d / "Dockerfile").write_text(dockerfile_for(spec, self.base[key]["id"], self.base[key]["user"]))
            tag = self._tag(spec)
            env = {**os.environ, "DOCKER_BUILDKIT": "0"}
            self._run("docker", "build", "--pull=false", "--network=none", "-t", tag, str(d), env=env)
            self.new_ids[key] = self._image_id(tag)
            self.record("built", {"target": key})
        (self.OP / "new-images.json").write_text(json.dumps(self.new_ids, indent=1))

    def clean_build(self):
        shutil.rmtree(self.CTX, ignore_errors=True)
        assert not self.CTX.exists(), "build context left behind"

    def _tag(self, spec):
        return f"minimoi-staging/{spec['repo']}:{RELEASE}"

    def check_new_images(self):
        for key, spec in self.targets.items():
            if spec.get("kind") == "config":
                self._check_gateway_config(spec)
                continue
            new_id = self.new_ids[key]
            check_overlay(json.loads(self._run("docker", "image", "inspect", self.base[key]["id"]))[0], json.loads(self._run("docker", "image", "inspect", new_id))[0],
                          image_env=spec.get("image_env", ()))
            want = want_hashes(spec["files"])
            check_files(want, self._hashes_in_image(new_id, sorted(want)), "built image for " + spec["container"])       # what is inside, not what went in
            for path in spec.get("executables", ()):
                self._sealed(new_id, "test", "-x", path)
            if spec.get("build") == "mc":
                self._sealed(new_id, "test", "!", "-e", MC_DIR + ".beta-check-6c.sh")
            if spec.get("smoke"):
                self._sealed(new_id, spec["smoke"][0], *spec["smoke"][1:], extra=("-e", "PYTHONDONTWRITEBYTECODE=1"))
            if spec.get("kind") == "node":
                for path in want:
                    if path.endswith((".mjs", ".cjs")):
                        self._sealed(new_id, "node", "--check", path)
            else:
                bad = json.loads(self._sealed(new_id, "python", "-c", IMPORT_CHECK, "/app", *[d for d in sorted(want) if d.endswith(".py")]).strip().splitlines()[-1])
                assert not bad, f"{spec['container']}: imports that do not resolve in the built image: {bad[:10]}"
                self.layout_report[key] = json.loads(self._sealed(new_id, "python", "-c", LAYOUT_CHECK, input=json.dumps(spec.get("layout_files", {}))).strip().splitlines()[-1])
            if spec.get("build") == "cos":
                self._sealed(new_id, "grep", "-q", "BEGIN_MINIMOI_MC_HANDBOOK 0.1.2", COS_DIR + "AGENTS.md")
                self._sealed(new_id, "test", "-f", COS_DIR + "ROLE_LOAD.json")
                env = [x for e in self.O.inspect(spec["container"])["Config"]["Env"] if e.split("=")[0] in AGENT_ENV for x in ("-e", e)]
                self._run("docker", "run", "--rm", "--network", "none", "--cap-drop", "ALL", *env, "--entrypoint", "sh", new_id, "-c",
                          f"cp {COS_DIR}openclaw.json /tmp/c.json && node {COS_DIR}plan-access.cjs /tmp/c.json "
                          "&& node -e \"const f=require('fs'),c=JSON.parse(f.readFileSync('/tmp/c.json'));c.agents.ownership='explicit';f.writeFileSync('/tmp/c.json',JSON.stringify(c))\" "
                          "&& OPENCLAW_CONFIG_PATH=/tmp/c.json node /app/openclaw.mjs config validate --json")
        (self.OP / "layout-report.json").write_text(json.dumps(self.layout_report, indent=1))

    def _check_gateway_config(self, spec):
        """The gateway's own YAML reader, on the new file: it parses, and no route a caller uses today disappears."""
        name = next(iter(spec["files"]))
        new, live = self.REL / "payload" / name, self.R / "config" / spec["config"]
        code = ("import yaml;a=yaml.safe_load(open('/new.yaml'));b=yaml.safe_load(open('/live.yaml'));names=lambda d:{m['model_name'] for m in d['model_list']};"
                "assert names(b)<=names(a),'a route name disappeared'")
        self._sealed(self.O.inspect(spec["container"])["Image"], "python", "-c", code, extra=("-v", f"{new}:/new.yaml:ro", "-v", f"{live}:/live.yaml:ro"))

    def _images(self, wrapper):
        return {self.where[k][1]: self._tag(s) for k, s in self.targets.items() if self.where[k][0] == wrapper and s.get("kind") != "config"}

    def check_render(self):
        for wrapper, override in self.OVERRIDES.items():
            before, images = self._render(wrapper), self._images(wrapper)
            tmp = self.OP / f"check.{wrapper}.private.yml"
            tmp.write_text(json.dumps(override_doc(images), indent=2))
            tmp.chmod(0o600)
            try:
                after = self._render(wrapper, tmp)
            finally:
                tmp.unlink(missing_ok=True)
            only_images(before, after, images)

    def plan(self):
        order, files = [], {}
        for tier in sorted({s["tier"] for s in self.targets.values()}):
            for key in self._targets_by_tier(tier):
                spec = self.targets[key]
                order.append({"container": spec["container"], "image": spec.get("config") or self._tag(spec), "from": self.base.get(key, {}).get("id", "host config file"),
                              "files": len(spec["files"]), "down": spec.get("down", "")})
                files[spec["container"]] = {m["dest"]: name for name, m in spec["files"].items()}
        plan = {"release": RELEASE, "commit": self.meta["commit"], "recreate_in_order": order, "skipped": self.meta["skipped"], "layout": {k: {x: len(v[x]) for x in v} for k, v in self.layout_report.items()},
                "not_on_declared_base": self.off_base,
                "changed": "nothing live; overlay images built, build context removed"}
        (self.OP / "plan.json").write_text(json.dumps({**plan, "files": files, "layout_detail": self.layout_report}, indent=1))
        for row in order:
            print(f"WILL RECREATE {row['container']:30} {row['files']:4} files  -> {row['image']}  down: {row['down']}", flush=True)
        for name, why in self.meta["skipped"].items():
            print(f"SKIP {name}: {why}", flush=True)
        for name, off in self.off_base.items():
            print(f"NOTE {name}: {len(off)} file(s) run a committed version other than the tag's own commit; replaced all the same; see plan.json", flush=True)
        for key, rep in self.layout_report.items():
            if rep["differs"] or rep["missing"]:
                print(f"LAYOUT {key}: {len(rep['differs'])} other file(s) differ from the commit, {len(rep['missing'])} missing; see plan.json", flush=True)
        return plan

    # save
    def _override_files(self):
        """Every compose file a Dev container was made from, and the host overlays. They must end byte-identical."""
        files = set()
        for name in ("minimoi-portal", "minimoi-mc-agent"):
            labels = self.O.inspect(name)["Config"].get("Labels") or {}
            files.update(p for p in (labels.get("com.docker.compose.project.config_files") or "").split(",") if p)
        files.update(map(str, [self.O.PF, self.O.MC_FINAL, *self.O.MC_FILES]))
        out = sorted(p for p in files if pathlib.Path(p).is_file())
        for p in out:
            refuse_production(p)
        return out

    def save_before(self):
        self.BEFORE.mkdir(mode=0o700, exist_ok=True)
        gateway = self.targets.get("model-gateway")
        self.gateway_file = self.R / "config" / gateway["config"] if gateway else None
        for wrapper, path in self.WRAPPERS.items():
            shutil.copy2(path, self.BEFORE / f"wrapper.{wrapper}.private")
        shutil.copy2(self.CAT, self.BEFORE / "catalogue.json")
        if self.gateway_file:
            shutil.copy2(self.gateway_file, self.BEFORE / "gateway-config.yaml")
        state = next(v["Source"] for v in self.O.inspect(self.O.MC)["Mounts"] if v["Destination"] == "/home/node/.openclaw")
        shutil.copy2(pathlib.Path(state) / "openclaw.json", self.BEFORE / "runtime.private.json")
        overrides = {p: hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in self._override_files()}
        for i, p in enumerate(sorted(overrides)):
            shutil.copy2(p, self.BEFORE / f"override.{i}.private")
        saved = {"images": {}, "tags": {}, "contracts": {}, "dev_values": {}, "overrides": overrides, "state": state, "where": self.where, "new_ids": self.new_ids,
                 "others": self._others(), "renders": {w: self._render(w) for w in self.WRAPPERS}, "catalogue_sha": hashlib.sha256(self.CAT.read_bytes()).hexdigest()}
        for key, spec in self.targets.items():
            live = self.O.inspect(spec["container"])
            saved["images"][key], saved["tags"][key] = live["Image"], live["Config"]["Image"]
            saved["contracts"][key] = json.loads(json.dumps(self.O.contract(spec["container"])))
            saved["dev_values"][key] = dev_values(live["Config"]["Env"])
        (self.BEFORE / "state.private.json").write_text(json.dumps(saved))
        os.chmod(self.BEFORE / "state.private.json", 0o600)
        self.saved = saved

    def check_saved(self):
        assert (self.BEFORE / "state.private.json").exists(), "nothing was applied: no saved state"
        self.saved = json.loads((self.BEFORE / "state.private.json").read_text())
        self.where = {k: tuple(v) for k, v in self.saved["where"].items()}
        self.others_strict = False                  # a rollback can come much later: other containers may have moved on their own
        self.new_ids = self.saved["new_ids"]
        gateway = self.targets.get("model-gateway")
        self.gateway_file = self.R / "config" / gateway["config"] if gateway else None

    def _others(self):
        mine = {s["container"] for s in self.targets.values()} | {self._backup(s) for s in self.targets.values()}
        return {k: v for k, v in self.O.snapshot().items() if k not in mine}

    # swap
    def stop_workers(self):
        running = [w for w in WORKERS if w in self.O.snapshot()]
        if running:
            self._run("docker", "stop", "--time", str(WORKER_STOP_SECONDS), *running)

    def start_workers(self):
        self._run("docker", "start", *WORKERS)

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
        tmp = path.with_suffix(path.suffix + ".beta-tmp")
        tmp.write_text(text)
        os.chmod(tmp, stat.st_mode & 0o777)
        os.chown(tmp, stat.st_uid, stat.st_gid)
        os.replace(tmp, path)

    def write_new(self):
        for wrapper, override in self.OVERRIDES.items():
            override.write_text(json.dumps(override_doc(self._images(wrapper)), indent=2))
            override.chmod(0o600)
            path = self.WRAPPERS[wrapper]
            self._write_file(path, with_override(path.read_text(), str(override)), path)
        if self.gateway_file:
            spec = self.targets["model-gateway"]
            self._write_file(self.gateway_file, (self.REL / "payload" / next(iter(spec["files"]))).read_text(), self.gateway_file)

    def _wait_http(self, spec, gate):
        if not spec.get("health"):
            return
        code = f"import urllib.request as u;assert u.urlopen({spec['health']!r},timeout=10).status==200"
        end = time.monotonic() + spec.get("wait", DEFAULT_WAIT)
        while True:
            p = subprocess.run(["docker", "exec", spec["container"], "python", "-c", code], capture_output=True, timeout=60)
            if p.returncode == 0:
                return
            if time.monotonic() > end:
                raise RuntimeError(spec["container"] + " did not answer its health URL")
            if gate is not None:
                assert self.O.gate_alive(gate), "Admission gate lost"
            time.sleep(3)

    def recreate(self, tier, gate):
        for key in self._targets_by_tier(tier):
            spec = self.targets[key]
            wrapper, service = self.where[key]
            if key == "mc-agent":                   # a SIGKILL before OpenClaw frees its lease costs minutes on the next start
                subprocess.run(["docker", "stop", "--time", "120", spec["container"]], capture_output=True, timeout=180)
            force = ("--force-recreate",) if spec.get("kind") == "config" else ()
            self._compose(wrapper, "up", "-d", "--no-deps", "--no-build", "--pull", "never", *force, service)
            self.O.wait_healthy(spec["container"], gate, spec["wait"])
            self._wait_http(spec, gate)
            self.record("recreated", {"container": spec["container"]})

    # A standalone worker keeps every setting: stop, keep the old container renamed, create the replacement from its exact config.
    def _backup(self, spec):
        return spec["container"] + "-before-" + RELEASE

    def _api(self, method, path, body=None):
        import http.client
        import socket

        class Docker(http.client.HTTPConnection):
            def connect(self):
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.connect("/var/run/docker.sock")
        conn = Docker("localhost", timeout=90)
        conn.request(method, path, None if body is None else json.dumps(body), {"Content-Type": "application/json"})
        r = conn.getresponse()
        raw, status = r.read(), r.status
        conn.close()
        if status >= 300:
            raise RuntimeError(f"Docker API refused {method} {path.split('?')[0]} ({status})")
        return json.loads(raw) if raw else {}

    def _swap_standalone(self, spec, new_id):
        name, backup = spec["container"], self._backup(spec)
        old = self.O.inspect(name)
        assert not old["State"]["Running"], name + " is still running"
        (self.BEFORE / f"{name}.private.json").write_text(json.dumps(old))
        self._run("docker", "update", "--restart=no", name)
        self._api("POST", f"/containers/{name}/rename?name={backup}")
        config = dict(old["Config"])
        config.pop("Hostname", None)
        config["Image"] = new_id
        endpoints = {n: {"Aliases": [a for a in x.get("Aliases") or [] if a not in (old["Id"], old["Id"][:12])], "IPAMConfig": x.get("IPAMConfig")}
                     for n, x in old["NetworkSettings"]["Networks"].items()}
        primary = primary_network(old["HostConfig"]["NetworkMode"], old["NetworkSettings"]["Networks"])
        self._api("POST", "/containers/create?name=" + name, {**config, "HostConfig": old["HostConfig"], "NetworkingConfig": {"EndpointsConfig": {primary: endpoints[primary]}}})
        for n, x in endpoints.items():
            if n != primary:
                self._api("POST", f"/networks/{n}/connect", {"Container": name, "EndpointConfig": x})

    def _restore_standalone(self, spec):
        name, backup = spec["container"], self._backup(spec)
        exists = lambda n: subprocess.run(["docker", "inspect", n], capture_output=True).returncode == 0
        if not exists(backup):
            return                                  # never swapped: its own container stands
        if exists(name):
            subprocess.run(["docker", "stop", "--time", "75", name], capture_output=True, timeout=120)
            self._run("docker", "rm", name)
        self._api("POST", f"/containers/{backup}/rename?name={name}")
        old = json.loads((self.BEFORE / f"{name}.private.json").read_text())
        self._api("POST", f"/containers/{name}/update", {"RestartPolicy": old["HostConfig"]["RestartPolicy"]})

    def create_workers(self, gate):
        for key, spec in sorted(self.targets.items()):
            if not spec.get("worker"):
                continue
            wrapper, service = self.where[key]
            if wrapper:
                self._compose(wrapper, "up", "--no-start", "--no-deps", "--no-build", "--pull", "never", service)
            else:
                self._swap_standalone(spec, self.new_ids[key])
            self.record("worker_created", {"container": spec["container"]})

    def _check_dev_state(self):
        check_catalogue(json.loads(self.CAT.read_text()))
        assert hashlib.sha256(self.CAT.read_bytes()).hexdigest() == self.saved["catalogue_sha"], "the host catalogue changed"
        for path, want in self.saved["overrides"].items():
            assert hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest() == want, "an existing override changed: " + path
        if self.others_strict:
            now = self._others()
            moved = sorted(k for k in self.saved["others"] if now.get(k) != self.saved["others"][k])
            assert not moved, "an unrelated container changed: " + ", ".join(moved[:8])
            appeared = sorted(set(now) - set(self.saved["others"]))
            if appeared:                            # compose can hold a short-lived container mid-swap; noted, not fatal
                self.record("others_appeared", {"containers": appeared[:8]})
        elif self._others() != self.saved["others"]:
            self.record("others_moved_since_apply", {"note": "not this rollback's doing"})

    def verify_new(self):
        for key, spec in self.targets.items():
            live = self.O.inspect(spec["container"])
            if spec.get("kind") != "config":
                assert live["Image"] == self.new_ids[key], "not the new image: " + spec["container"]
            else:
                self._check_gateway_serves(spec)
            check_restored(with_image_env(self.saved["contracts"][key], spec), sorted_env(self.O.contract(spec["container"])), "contract of " + spec["container"])
            assert dev_values(live["Config"]["Env"]) == self.saved["dev_values"][key], "a Dev setting moved: " + spec["container"]
            check_required_env(spec["container"], live["Config"]["Env"])
            if spec.get("worker"):
                continue                            # started and read back after this swap is verified
            self._check_running_files(spec, live)
        self._check_dev_state()
        for wrapper in self.WRAPPERS:
            only_images(self.saved["renders"][wrapper], self._render(wrapper), self._images(wrapper))
        check_mc_policy(json.loads((pathlib.Path(self.saved["state"]) / "openclaw.json").read_text()))
        self._check_portal_answers()

    def _check_running_files(self, spec, live):
        if spec.get("kind") == "config":
            return                                  # the host file is the payload; hashed in restore and in the gateway config check
        assert live["State"]["Running"] and live["State"].get("Health", {}).get("Status") in (None, "healthy"), "not healthy: " + spec["container"]
        want = want_hashes(spec["files"])
        picked = sample(want, [m["Destination"] for m in live["Mounts"]])
        check_files({d: want[d] for d in picked}, self._hashes_in_container(spec["container"], picked), "running " + spec["container"])
        self.record("files_verified", {"container": spec["container"], "sampled": len(picked), "of": len(want)})

    def _check_gateway_serves(self, spec):
        """The host's file is the reviewed one, and the recreated gateway reads those bytes."""
        want = next(iter(spec["files"].values()))["after"]
        assert hashlib.sha256(self.gateway_file.read_bytes()).hexdigest() == want, "gateway config on the host differs"
        code = "import hashlib;print(hashlib.sha256(open('/app/config.yaml','rb').read()).hexdigest())"
        assert self._run("docker", "exec", spec["container"], "python", "-c", code).strip() == want, "the gateway serves another config"

    def _check_portal_answers(self):
        code = ("import http.client,sys,json\n"
                "out={}\n"
                f"for p in {list(PORTAL_ROUTES)!r}:\n"
                " c=http.client.HTTPConnection('127.0.0.1',5001,timeout=15);c.request('GET',p);out[p]=c.getresponse().status\n"
                f"c=http.client.HTTPConnection('127.0.0.1',{MC_TOOLS_PORT},timeout=15);c.request('GET','/');out['tools']=c.getresponse().status\n"
                "print(json.dumps(out))")
        got = json.loads(self._run("docker", "exec", "minimoi-portal", "python", "-c", code).strip().splitlines()[-1])
        for path in PORTAL_ROUTES:
            assert got[path] != 404, "route missing: " + path
        assert isinstance(got["tools"], int), "the MC tools listener did not answer"
        self.record("portal_answers", got)

    def verify_workers(self):
        for key, spec in self.targets.items():
            if spec.get("worker"):
                self.O.wait_healthy(spec["container"], None, spec["wait"])
                live = self.O.inspect(spec["container"])
                assert live["State"]["Running"] and live["Image"] == self.new_ids[key], "worker: " + spec["container"]
                if spec["container"].startswith("minimoi-guildhall"):
                    self._heartbeat(spec["container"])
                self._check_running_files(spec, live)

    def _heartbeat(self, name):
        code = "import pathlib,time;p=pathlib.Path('/tmp/rooms-worker/alive');assert p.exists() and time.time()-p.stat().st_mtime<30"
        for _ in range(30):
            if subprocess.run(["docker", "exec", name, "python", "-c", code], capture_output=True).returncode == 0:
                return
            time.sleep(2)
        raise RuntimeError(name + " did not publish a fresh worker heartbeat")

    def restore_verified(self, gate):
        for wrapper, path in self.WRAPPERS.items():
            shutil.copy2(self.BEFORE / f"wrapper.{wrapper}.private", path)
        for override in self.OVERRIDES.values():
            override.unlink(missing_ok=True)
        for i, p in enumerate(sorted(self.saved["overrides"])):
            if hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() != self.saved["overrides"][p]:
                shutil.copy2(self.BEFORE / f"override.{i}.private", p)
        shutil.copy2(self.BEFORE / "catalogue.json", self.CAT)
        if self.gateway_file:
            self._write_file(self.gateway_file, (self.BEFORE / "gateway-config.yaml").read_text(), self.gateway_file)
        state = pathlib.Path(self.saved["state"])
        subprocess.run(["docker", "stop", "--time", "120", "minimoi-mc-agent"], capture_output=True, timeout=180)
        cfg, tmp = state / "openclaw.json", state / "openclaw.json.restore"
        st = cfg.stat()
        shutil.copy2(self.BEFORE / "runtime.private.json", tmp)
        os.chown(tmp, st.st_uid, st.st_gid)
        os.replace(tmp, cfg)
        for tier in Deploy.TIERS:
            self.recreate(tier, gate)
        for key, spec in sorted(self.targets.items()):
            if spec.get("worker"):
                wrapper, service = self.where[key]
                if wrapper:
                    self._compose(wrapper, "up", "--no-start", "--no-deps", "--no-build", "--pull", "never", service)
                else:
                    self._restore_standalone(spec)
        for key, spec in self.targets.items():
            live = self.O.inspect(spec["container"])
            assert live["Image"] == self.saved["images"][key], "restored image differs: " + spec["container"]
            check_restored(self.saved["contracts"][key], self.O.contract(spec["container"]), "contract of " + spec["container"])
        for wrapper, path in self.WRAPPERS.items():
            assert path.read_bytes() == (self.BEFORE / f"wrapper.{wrapper}.private").read_bytes(), "restored wrapper differs"
            check_restored(self.saved["renders"][wrapper], self._render(wrapper), "compose render")
        if self.gateway_file:
            assert self.gateway_file.read_bytes() == (self.BEFORE / "gateway-config.yaml").read_bytes(), "restored gateway config differs"
        self._check_dev_state()
        same_runtime(json.loads((self.BEFORE / "runtime.private.json").read_text()), json.loads(cfg.read_text()))
        self.record("restored", {"status": "previous images, overrides and config proved"})

    def verify_restored_workers(self):
        for key, spec in self.targets.items():
            if spec.get("worker"):
                self.O.wait_healthy(spec["container"], None, spec["wait"])
                assert self.O.inspect(spec["container"])["Image"] == self.saved["images"][key], "restored worker: " + spec["container"]


def main(argv):                                     # pragma: no cover
    import fcntl
    here = pathlib.Path(__file__).resolve().parent
    sys.path.insert(0, str(here))
    from guarded_swap import guarded_swap
    action = argv[1]
    assert action in ("dry-run", "apply", "rollback"), "dry-run, apply or rollback"
    host = RealHost()
    status = host.OP / "status.json"
    with (host.R / ".renewal-deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)    # refused before status.json: a second start must not overwrite a run's state
        status.write_text(json.dumps({"action": action, "state": "running", "at": time.time()}))
        try:
            deploy = Deploy(host, guarded_swap)
            result = {"dry-run": deploy.dry_run, "apply": deploy.apply, "rollback": deploy.rollback}[action]()
        except BaseException as exc:
            status.write_text(json.dumps({"action": action, "state": "failed", "exception": type(exc).__name__, "message": str(exc)[:400], "at": time.time()}))
            raise
        print("RESULT", result, flush=True)
        status.write_text(json.dumps({"action": action, "state": "done", "result": str(result), "at": time.time()}))


if __name__ == "__main__":                          # pragma: no cover
    main(sys.argv)
