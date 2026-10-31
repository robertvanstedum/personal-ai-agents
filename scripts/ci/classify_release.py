#!/usr/bin/env python3
"""Map changed repository paths to the smallest safe production service set."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional


ALL_SERVICES = (
    "portal",
    "curator",
    "german",
    "portuguese",
    "system-bot",
    "cos-bot",
    "cos-scheduler",
    "cos-agent-a",
    "model-gateway",
)
PYTHON_SERVICES = ALL_SERVICES[:7]


def _is_release_only(path: str) -> bool:
    exact = {
        "README.md", "README.pdf", "ARCHITECTURE.md", "ARCHITECTURE.pdf",
        "OPERATIONS.md", "OPERATIONS.pdf", "ROADMAP.md", "ROADMAP.pdf",
        "data/guild/build_queue.json", "scripts/sync_docs.sh",
        # The classifier itself runs in CI (deploy.yml) and is never part of a
        # service image, so changing it redeploys nothing.
        "scripts/ci/classify_release.py",
        # Chooses the commit the classifier diffs against; CI only, like the
        # classifier, and never in a service image.
        "scripts/ci/release_base.sh",
        # Chooses which tests CI runs; CI only, never in a service image.
        "scripts/ci/select_tests.py",
        # A nightly full-suite run on GitHub's runners; it deploys nothing.
        ".github/workflows/nightly-tests.yml",
    }
    return (
        path in exact
        or path.startswith("docs/")
        or path.startswith("scripts/docs/")
        or path.startswith("tests/")
        # Prototype Lab and Planning Studio project homes are documentation:
        # briefs, specifications, decision records, and preserved evidence. A
        # promoted prototype's runnable code lives with it (for example
        # prototype-lab/projects/<name>/Dockerfile) and is released by its own
        # tag workflow, never by this pipeline, so no mini-moi service restarts
        # for a change under these trees.
        or path.startswith("prototype-lab/")
        or path.startswith("planning-studio/")
        # The Mac's scheduled jobs and their launchd files (job-observability): run on the laptop, in no service image.
        or path.startswith(("scripts/jobs/", "scripts/memory/", "infrastructure/launchd/"))
        or path in {"requirements.test.txt", "pytest.ini", ".gitignore"}
        or _is_staging_only(path)
        or path.startswith(DORMANT_PREFIXES)
        or path.startswith(LOCAL_TOOL_PREFIXES)
        or path in LOCAL_TOOL_FILES
        or ((path.startswith(VAULT_LOCAL_PREFIXES) or path in VAULT_LOCAL_FILES) and path not in VAULT_SERVER_FILES)
        or path in GUILD_LOCAL_TOOLS
        or path in MC_RUNNER_LOCAL_FILES
        or path.startswith("services/agent_installer/")
        or path.startswith(OPS_ONLY_PREFIXES)
        or path in OPS_ONLY_FILES
        or path.startswith(MC_IMAGE_PREFIXES)
    )


# Operator scripts no service image runs. scripts/credentials/ holds the
# laptop's Keychain tools and the EC2 host's secret sync; none is imported or
# started by a service (tests/test_release_classifier.py guards this). The host
# sync (sync_cos_runtime_secrets_from_ssm.sh) is pushed to EC2 and run by every
# deploy in deploy.yml, so an edit to it alone reaches the host with the next
# service deploy, which pushes it before running it. MC's host scripts ship in
# MC's own image through deploy-mc-agent.yml (docker/mc-agent-container), never
# through this pipeline.
OPS_ONLY_PREFIXES = ("scripts/credentials/",)
OPS_ONLY_FILES = frozenset({"scripts/operations/deploy_mc_container_ec2.sh",
                            "scripts/operations/mc_prod_db_migrate.sh"})


# MC's own container: built and deployed only by deploy-mc-agent.yml on an mc-agent-v* tag. The portal image copies the
# repository, but no service reads these files (tests/test_release_classifier.py guards this).
MC_IMAGE_PREFIXES = ("docker/mc-agent-container/",)


# docker-compose.prod.yml: a change restarts only the services whose own
# block changed. Each production Compose service is owned by the classifier
# service deploy_scoped_release.sh recreates for it; vault-ask runs the portal
# image and is recreated beside the portal. postgres is absent on purpose: the
# scoped deploy never recreates it (--no-deps), so its block keeps the full
# fallback, as does any other service or top-level key (networks, volumes).
PROD_COMPOSE = "docker-compose.prod.yml"
COMPOSE_SERVICE_OWNERS = {**{service: (service,) for service in ALL_SERVICES}, "vault-ask": ("portal",),
                          "supervisor": ("portal",)}


# Top-level definitions a service uses by name: they are part of its rendered config.
COMPOSE_NAMED = ("volumes", "networks", "secrets", "configs")


def _compose_references(service: dict, kind: str) -> set[str]:
    entries = service.get(kind)
    if kind == "networks" and entries is None and "network_mode" not in service:
        return {"default"}
    if isinstance(entries, dict):
        return {str(name) for name in entries}
    names: set[str] = set()
    for entry in entries or ():
        if isinstance(entry, dict):
            entry = entry.get("source") if entry.get("type", "volume") == "volume" or kind != "volumes" else None
        elif isinstance(entry, str) and kind == "volumes":
            entry = entry.split(":", 1)[0]
            entry = None if entry.startswith(("/", ".", "~", "$")) else entry          # a bind mount, not a named volume
        if entry:
            names.add(str(entry))
    return names


def _compose_view(config: dict, name: str):
    """A service's rendered config: its block plus the top-level definitions it names."""
    service = (config.get("services") or {}).get(name)
    if not isinstance(service, dict):
        return service
    named = {kind: {ref: (config.get(kind) or {}).get(ref) for ref in sorted(_compose_references(service, kind))}
             for kind in COMPOSE_NAMED}
    return service, named


def compose_changed_services(base_text: Optional[str], head_text: Optional[str]) -> Optional[tuple[str, ...]]:
    """Classifier services whose rendered Compose config differs, or None when only a full release is safe.

    Compares the parsed YAML (anchors and merge keys resolved; comments and
    layout ignored), so a comment-only edit restarts nothing. A service's
    config includes the top-level volumes, networks, secrets and configs it
    names, so a new named volume restarts only the service that mounts it.
    None for a file that is missing on either side or fails to parse, for any
    other top-level change (x-* extension fields aside), and for a service that
    is added, removed or changed without an owner.
    """
    try:
        import yaml
    except ImportError:
        return None
    if base_text is None or head_text is None:
        return None
    try:
        base, head = yaml.safe_load(base_text), yaml.safe_load(head_text)
    except yaml.YAMLError:
        return None
    if not isinstance(base, dict) or not isinstance(head, dict):
        return None
    for config in (base, head):
        if not isinstance(config.get("services") or {}, dict) or not all(
                isinstance(config.get(kind) or {}, dict) for kind in COMPOSE_NAMED):
            return None
    # x-* extension fields do nothing themselves; their anchors are already
    # resolved into the services that merge them.
    def rest(config: dict) -> dict:
        return {k: v for k, v in config.items() if k not in ("services", *COMPOSE_NAMED) and not str(k).startswith("x-")}
    if rest(base) != rest(head):
        return None
    owners: set[str] = set()
    for name in set(base.get("services") or {}) | set(head.get("services") or {}):
        before, after = _compose_view(base, name), _compose_view(head, name)
        if before == after:
            continue
        if before is None or after is None or name not in COMPOSE_SERVICE_OWNERS:
            return None
        owners.update(COMPOSE_SERVICE_OWNERS[name])
    return tuple(service for service in ALL_SERVICES if service in owners)


def git_compose_diff(base: str, head: str, cwd: Optional[Path] = None) -> Callable[[], Optional[tuple[str, ...]]]:
    """compose_diff for classify(): the services whose block changed between two commits."""
    def show(commit: str) -> Optional[str]:
        result = subprocess.run(["git", "show", f"{commit}:{PROD_COMPOSE}"], capture_output=True, text=True, cwd=cwd)
        return result.stdout if result.returncode == 0 else None
    return lambda: compose_changed_services(show(base), show(head))


# Files that no production service is built from or mounts yet. Master
# Craftsman's own image (docker/Dockerfile.mc-agent) and its config, workspace
# seed and scripts run only in MC's own staging Compose project
# (docker-compose.mc.yml, scripts/staging/mc.sh); production has no MC service
# until the production MC spec. tests/test_release_classifier.py fails if
# deploy.yml, the deploy script or docker-compose.prod.yml starts using them,
# so that change must classify them as a real production service.
DORMANT_PREFIXES = ("docker/mc-agent/", "docker/Dockerfile.mc-agent",
                    # Records (Rooms) runs only in its own staging project (Guild 1.1 slice 4).
                    "docker/Dockerfile.records", "docker/requirements.records.txt",
                    # The Rooms worker (Rooms R1) runs only beside Records in that project.
                    "docker/Dockerfile.rooms-worker", "docker/requirements.rooms-worker.txt", "docker/Dockerfile.rooms-codex",
                    "docker/fetch_codex.py", "services/rooms_worker/",
                    # Rooms R2: the Mac connector and the door sidecar, staging/Mac only.
                    "services/rooms_connector/", "services/records_door/")


# The Mac Docker staging stack (dev.minimoi.ai, scripts/staging/README.md).
# Production never reads these paths: EC2 receives only docker-compose.prod.yml
# and the model-gateway image bakes litellm.prod.yaml. docker-compose.prod.yml
# itself is NOT staging-only and keeps the full-deploy fallback.
STAGING_ONLY_FILES = frozenset({
    "docker-compose.staging.yml",
    # Master Craftsman's own Compose project (MC spec v0.9 §3), staging only.
    "docker-compose.mc.yml",
    # The gateway's key database overlay (MC stage C), staging only.
    "docker-compose.staging-keys.yml",
    # CoS's own capped gateway key (cos.sh key), staging only.
    "docker-compose.staging-cos-key.yml",
    # The CoS turn log, scheduled-jobs and MC turn-log mounts, staging only.
    "docker-compose.staging-cos-turns.yml",
    "docker-compose.staging-jobs.yml",
    "docker-compose.staging-mc-turns.yml",
    # Records (Rooms)' own Compose project (Guild 1.1 slice 4), staging only.
    "docker-compose.records.yml",
    "services/model_gateway/litellm.staging.yaml",
})
STAGING_ONLY_PREFIXES = ("scripts/staging/",)


# Operator tools that run on the owner's laptop: no production service image
# runs them (the Dockerfiles copy the repository, but nothing starts these).
LOCAL_TOOL_PREFIXES = ("scripts/workshop/", "scripts/vault/", "tools/workshop/", "scripts/release/", "scripts/dev/")
# Exact laptop operator entrypoints; no production service starts or mounts these.
LOCAL_TOOL_FILES = frozenset({"scripts/mc/morning.py", "scripts/mc/regression.py", "scripts/rooms/archive.py"})

# Workshop readers/writers remain portal-owned, with laptop-only callers.
# The two exact scrub modules also serve the CoS Vault context renderer below;
# changing them must rebuild all three production consumers. Import guards keep
# expected laptop homes separate from any newly introduced production consumer.
WORKSHOP_BACKEND_PREFIXES = ("core/workshop_journal/", "core/vault_t1/", "core/agent_turns/", "core/jobs/")
SHARED_SCRUB_FILES = frozenset({"utils/credential_scrub.py", "utils/payment_scrub.py"})

# The remote consumer, renderer and citation checker serve CoS and are also
# loaded transitively by the portal regression reader; rebuild all consumers.
# The actual Ask service/query log, shelf, index and backup remain on the laptop.
# Unknown neighbouring paths retain the conservative fallback; import guards
# fail if a production service starts depending on a laptop-only module.
VAULT_COS_FILES = frozenset({"core/memory_ask/consumer.py", "core/memory_ask/context.py", "core/memory_ask/citation.py"})
# The production Vault Ask service (vault-ask, behind the compose `vault` profile) runs the portal image, so every module
# it imports (measured 17 Oct 2026: import core.memory_ask.service with sqlite_vec absent) must rebuild and redeploy the
# portal; deploy_scoped_release.sh then recreates vault-ask beside it when that container exists.
VAULT_SERVER_FILES = frozenset({
    "core/memory_ask/__init__.py",
    "core/memory_ask/ask.py",
    "core/memory_ask/citation.py",
    "core/memory_ask/identity.py",
    "core/memory_ask/querylog.py",
    "core/memory_ask/service.py",
    "core/memory_ask/tool.py",
    "core/memory_index/__init__.py",
    "core/memory_index/adjacent.py",
    "core/memory_index/chunker.py",
    "core/memory_index/cli.py",
    "core/memory_index/embedder.py",
    "core/memory_index/health.py",
    "core/memory_index/indexer.py",
    "core/memory_index/policy.py",
    "core/memory_index/resolver.py",
    "core/memory_index/search.py",
    "core/memory_index/store.py",
    "core/memory_shelf/__init__.py",
    "core/memory_shelf/bundle.py",
    "core/memory_shelf/codes.py",
    "core/memory_shelf/editions.py",
    "core/memory_shelf/events.py",
    "core/memory_shelf/fsio.py",
    "core/memory_shelf/ledger.py",
    "core/memory_shelf/record.py",
    "core/memory_shelf/render.py",
    "core/memory_shelf/review.py",
    "core/memory_shelf/sessions.py",
    "core/memory_shelf/shelf.py",
    "core/memory_shelf/ulid.py",
    "core/memory_shelf/weight.py",
})
VAULT_LOCAL_PREFIXES = ("core/memory_shelf/", "core/memory_index/", "core/memory_backup/", "core/memory_sync/")
# The Vault incremental sync (17 Oct 2026): the Mac runs vault-sync, the EC2 HOST runs vault-apply from the root-only tools bundle,
# and vault-apply's index step runs INSIDE the portal image (``docker run <portal image> /app/scripts/vault/vault_apply.py index-step``,
# because the host SQLite may not write the index's FTS5 table). So these must rebuild the portal image the index step executes;
# no service imports them.
VAULT_SYNC_PREFIXES = ("core/memory_sync/",)
# The production MC capture path (17 Oct 2026): the EC2 host's vault-mc-export runs the MC exporter INSIDE the portal container
# (``docker exec minimoi-portal python3 /app/scripts/vault/export_mc_conversations.py --stdout-tar``), so a change to the exporter must
# rebuild and redeploy the portal image it runs from. (vault_mc_export.py itself runs on the host from the tools bundle: no image.)
VAULT_SYNC_IMAGE_FILES = frozenset({"scripts/vault/vault_apply.py", "scripts/vault/export_mc_conversations.py"})
# The portal metadata view loads regression -> runner -> existing remote
# consumer/context/citation. Assessment is the coupled report-family reader;
# it currently depends on that pair, rather than being imported by the host.
# Route this exact reviewed family conservatively to portal, never documents.
REGRESSION_REPORT_FILES = frozenset({"core/memory_ask/assessment.py", "core/memory_ask/regression.py", "core/memory_ask/runner.py"})
VAULT_LOCAL_FILES = frozenset({"core/memory_ask/agents_cli.py", "core/memory_ask/ask.py", "core/memory_ask/canary_check.py",
                             "core/memory_ask/cli.py", "core/memory_ask/identity.py", "core/memory_ask/querylog.py",
                             "core/memory_ask/service.py", "core/memory_ask/tool.py",
                             # Pure shelf export seam: only fixture/laptop callers, never a portal entrypoint.
                             "core/workshop_journal/vault_export.py"})


# Agent request helpers are consumed by the Guild portal only. Shared voice
# also reads core.limits in the CoS images; keep that exact file distinct.
# Agent runners and installers also use these modules on the laptop;
# ownership tests fail if a
# second production consumer appears. Unknown core/scripts paths stay broad.
GUILD_BACKEND_PREFIXES = ("core/agent_requests/",)
# GitHub awareness: the observation module and its one-shot CLI run only in the
# portal image (it copies the repository); nothing else imports them.
GUILD_BACKEND_FILES = frozenset({"core/limits.py", "core/github_awareness.py", "scripts/mc/github_awareness.py"})
GUILD_LOCAL_TOOLS = frozenset({"scripts/agent_connection.py", "scripts/agent_requests.py"})

# The MC delegation runner (core/mc_delegation_runner/). Its one production consumer is the portal image, through:
# - the delegation dispatcher the portal runs (core/mc_delegation_dispatcher: store.py reads envelope.py, door.py reads
#   dispatcher_adapter.py), and
# - the front-door connections' code in core/agent_requests/ (portal-owned above), which reads launch_env.py, the one
#   environment policy for agent processes.
# Importing any of them runs the package's __init__.py. The Mac runner and the Mac connections use them too, from their
# own installed copies. The rest runs only in the Mac runner's own bundle (scripts/mc_host/install_delegation_runner.sh);
# no service image imports it. Exact files: a new module here keeps the conservative core/ fallback, and
# tests/test_release_classifier.py fails if another production consumer appears or the portal side reaches a
# runner-only file.
MC_RUNNER_PORTAL_FILES = frozenset({"core/mc_delegation_runner/__init__.py", "core/mc_delegation_runner/envelope.py",
                                    "core/mc_delegation_runner/dispatcher_adapter.py", "core/mc_delegation_runner/launch_env.py"})
MC_RUNNER_LOCAL_FILES = frozenset({"core/mc_delegation_runner/cli.py", "core/mc_delegation_runner/runner.py",
                                   "core/mc_delegation_runner/commands.py", "core/mc_delegation_runner/cost.py",
                                   "core/mc_delegation_runner/workspace.py"})


def _is_staging_only(path: str) -> bool:
    return path in STAGING_ONLY_FILES or path.startswith(STAGING_ONLY_PREFIXES)


def classify(paths: list[str],
             compose_diff: Optional[Callable[[], Optional[tuple[str, ...]]]] = None) -> tuple[str, tuple[str, ...]]:
    """compose_diff returns the services whose docker-compose.prod.yml block changed
    (or None when it cannot tell); without it a production Compose change is full."""
    services: set[str] = set()
    for path in (item.strip() for item in paths if item.strip()):
        if path == PROD_COMPOSE:
            changed = compose_diff() if compose_diff else None
            if changed is None:
                return "full", ALL_SERVICES
            services.update(changed)
            continue
        # Records is no longer an unrelated prototype: the approved production
        # profile reuses the portal image as its code consumer. Until the actual
        # production service/deploy closure is installed, retain full fallback
        # rather than inventing a deployable Records service in ALL_SERVICES.
        prefix = "prototype-lab/projects/project-records-room-poc/"
        relative = path.removeprefix(prefix)
        if (path.startswith(prefix) and not relative.startswith(("tests/", "docs/"))
                and not relative.endswith((".md", ".pdf"))):
            return "full", ALL_SERVICES
        if path in VAULT_SYNC_IMAGE_FILES or path.startswith(VAULT_SYNC_PREFIXES):
            services.add("portal")                                   # vault-apply's index step and the MC exporter run in the portal image
            continue
        if _is_release_only(path):
            continue
        if path.startswith("domains/german/"):
            services.update(("german", "system-bot"))
        elif path.startswith("domains/portuguese/"):
            services.add("portuguese")
        elif path.startswith("domains/curator/") or path.startswith("config/curator/"):
            services.update(("curator", "system-bot"))
        elif path.startswith("contracts/cos/"):
            services.add("cos-scheduler")          # copied into the scheduler image; the store writer reads it
        elif path.startswith("domains/cos/"):
            services.update(("cos-bot", "cos-scheduler"))
        elif path.startswith("domains/guild/"):
            services.update(("portal", "cos-bot", "cos-scheduler"))
        elif path.startswith("minimoi_portal/"):
            services.add("portal")
        elif path.startswith("services/usage/"):
            # The usage record (usage-record U1/U2): the CoS images copy it
            # (Dockerfile.cos*). The production gateway image does not; the
            # staging gateway mounts it. Production writes nothing until its
            # environment sets MINIMOI_USAGE_DIR. The portal imports it too
            # (streaming S1: MC's runtime-stream records; the portal image
            # copies the repository), so a change here redeploys the portal.
            services.update(("portal", "cos-bot", "cos-scheduler"))
        elif path.startswith("services/model_gateway/"):
            services.update(("model-gateway", "cos-bot", "cos-scheduler"))
        elif path.startswith("docker/cos-agent-a/") or path == "docker/Dockerfile.cos-agent-a":
            services.add("cos-agent-a")
        elif path.startswith("core/realtime_voice/"):
            services.update(("portal", "german", "portuguese", "cos-bot", "cos-scheduler"))
        elif path.startswith("core/telegram/"):
            services.update(("curator", "system-bot", "cos-bot"))
        elif path in SHARED_SCRUB_FILES:
            services.update(("portal", "cos-bot", "cos-scheduler"))
        elif path in REGRESSION_REPORT_FILES:
            services.add("portal")
        elif path in VAULT_COS_FILES:
            services.update(("portal", "cos-bot", "cos-scheduler"))
        elif path in VAULT_SERVER_FILES:
            services.add("portal")                                   # vault-ask shares the portal image
        elif path.startswith(WORKSHOP_BACKEND_PREFIXES) or path == "config/scheduled_jobs.json":
            services.add("portal")
        elif path == "core/limits.py":
            services.update(("portal", "cos-bot", "cos-scheduler"))
        elif path.startswith(GUILD_BACKEND_PREFIXES) or path in GUILD_BACKEND_FILES:
            services.add("portal")
        elif path in MC_RUNNER_PORTAL_FILES:
            services.add("portal")                                   # the portal's dispatcher and agent_requests read these
        elif path.startswith(("core/", "utils/")):
            services.update(PYTHON_SERVICES)
        elif path == "docker/Dockerfile.portal" or path == "docker/requirements.portal.txt":
            services.add("portal")
        elif path == "docker/Dockerfile.curator" or path == "docker/requirements.curator.txt":
            services.add("curator")
        elif path == "docker/Dockerfile.german" or path == "docker/requirements.german.txt":
            services.update(("german", "system-bot"))
        elif path == "docker/Dockerfile.portuguese" or path == "docker/requirements.portuguese.txt":
            services.add("portuguese")
        elif path == "docker/Dockerfile.telegram" or path == "docker/requirements.telegram.txt":
            services.add("system-bot")
        elif path in {
            "docker/Dockerfile.cos", "docker/Dockerfile.cos-bot",
            "docker/Dockerfile.cos-scheduler", "docker/requirements.cos-agent.txt",
        }:
            services.update(("cos-bot", "cos-scheduler"))
        elif path == "docker/Dockerfile.model-gateway":
            services.add("model-gateway")
        else:
            # Unknown ownership is a deliberate full-deploy fallback.
            return "full", ALL_SERVICES

    ordered = tuple(service for service in ALL_SERVICES if service in services)
    if not ordered:
        return "documents", ()
    return ("domain" if len(ordered) < len(ALL_SERVICES) else "full"), ordered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--base", help="commit the paths were diffed from (reads docker-compose.prod.yml per service)")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args()
    paths = args.paths or sys.stdin.read().splitlines()
    release_class, services = classify(paths, git_compose_diff(args.base, args.head) if args.base else None)
    result = {
        "release_class": release_class,
        "services": " ".join(services),
        "services_json": json.dumps(services),
    }
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as stream:
            for key, value in result.items():
                stream.write(f"{key}={value}\n")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
