#!/usr/bin/env python3
"""Map changed repository paths to the smallest safe production service set."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


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
        or path in {"requirements.test.txt", "pytest.ini", ".gitignore"}
        or _is_staging_only(path)
        or path.startswith(DORMANT_PREFIXES)
    )


# Files that no production service is built from or mounts yet. Master
# Craftsman's own image (docker/Dockerfile.mc-agent) and its config, workspace
# seed and scripts run only in MC's own staging Compose project
# (docker-compose.mc.yml, scripts/staging/mc.sh); production has no MC service
# until the production MC spec. tests/test_release_classifier.py fails if
# deploy.yml, the deploy script or docker-compose.prod.yml starts using them,
# so that change must classify them as a real production service.
#
# services/usage/ (the usage record, U1) is staging-only for now: the staging
# gateway mounts it (docker-compose.staging.yml); no production Dockerfile
# copies it and litellm.prod.yaml does not load it. The day a production image
# or config uses it, tests/test_release_classifier.py fails and this entry must
# become a real service mapping.
DORMANT_PREFIXES = ("docker/mc-agent/", "docker/Dockerfile.mc-agent", "services/usage/")


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
    "services/model_gateway/litellm.staging.yaml",
})
STAGING_ONLY_PREFIXES = ("scripts/staging/",)


def _is_staging_only(path: str) -> bool:
    return path in STAGING_ONLY_FILES or path.startswith(STAGING_ONLY_PREFIXES)


def classify(paths: list[str]) -> tuple[str, tuple[str, ...]]:
    services: set[str] = set()
    for path in (item.strip() for item in paths if item.strip()):
        if _is_release_only(path):
            continue
        if path.startswith("domains/german/"):
            services.update(("german", "system-bot"))
        elif path.startswith("domains/portuguese/"):
            services.add("portuguese")
        elif path.startswith("domains/curator/") or path.startswith("config/curator/"):
            services.update(("curator", "system-bot"))
        elif path.startswith("domains/cos/"):
            services.update(("cos-bot", "cos-scheduler"))
        elif path.startswith("domains/guild/"):
            services.update(("portal", "cos-bot", "cos-scheduler"))
        elif path.startswith("minimoi_portal/"):
            services.add("portal")
        elif path.startswith("services/model_gateway/"):
            services.update(("model-gateway", "cos-bot", "cos-scheduler"))
        elif path.startswith("docker/cos-agent-a/") or path == "docker/Dockerfile.cos-agent-a":
            services.add("cos-agent-a")
        elif path.startswith("core/realtime_voice/"):
            services.update(("german", "portuguese", "cos-scheduler"))
        elif path.startswith("core/telegram/"):
            services.update(("curator", "system-bot", "cos-bot"))
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
    args = parser.parse_args()
    paths = args.paths or sys.stdin.read().splitlines()
    release_class, services = classify(paths)
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
