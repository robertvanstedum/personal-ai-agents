"""B1 (a): the live Build Queue is a mounted FOLDER, never a baked or
single-file copy, in production and in Docker dev staging (review M2, C17)."""
from __future__ import annotations

import fnmatch
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
QUEUE_ENV = "GUILD_QUEUE_PATH=/app/runtime/guild/build_queue.json"


def _compose(name):
    return yaml.safe_load((REPO / name).read_text())["services"]


PROD_ROOT = "${MINIMOI_ROOT:-/opt/minimoi}"


def _volumes(service):
    # The production host root is parameterized for the Mac staging stack; with
    # MINIMOI_ROOT unset it renders as /opt/minimoi. Resolve that default here
    # so the colon inside ${VAR:-default} cannot shift the host:container split.
    return [v.replace(PROD_ROOT, "/opt/minimoi") for v in service.get("volumes", []) if isinstance(v, str)]


def test_prod_portal_mounts_the_queue_folder_not_the_file():
    services = _compose("docker-compose.prod.yml")
    portal = services["portal"]
    assert "/opt/minimoi/data/guild:/app/runtime/guild" in _volumes(portal)
    assert QUEUE_ENV in portal["environment"]
    for name, service in services.items():
        for volume in _volumes(service):
            target = volume.split(":")[1]
            assert not target.endswith("build_queue.json"), f"{name} still has a single-file queue mount"
            # C17: nothing may hide the image's data/guild/experiment_projection.json.
            assert target.rstrip("/") != "/app/data/guild", f"{name} mounts over /app/data/guild"


def test_dev_staging_portal_uses_a_state_folder_outside_worktrees():
    services = _compose("docker-compose.yml")
    portal = services["portal"]
    assert "${MINIMOI_STAGING_DATA:-~/minimoi-staging}/guild:/app/runtime/guild" in _volumes(portal)
    assert QUEUE_ENV in portal["environment"]
    for volume in _volumes(portal):
        assert not volume.split(":")[1].rstrip("/") == "/app/data/guild"


def test_no_other_service_reads_the_queue():
    """cos-bot, cos-scheduler and the rest never open build_queue.json, so they
    need no queue mount. Only the portal (and its queue store) reads it."""
    readers = set()
    for path in list(REPO.glob("domains/**/*.py")) + list(REPO.glob("minimoi_portal/**/*.py")) \
            + list(REPO.glob("core/**/*.py")) + list(REPO.glob("services/**/*.py")):
        if "build_queue" in path.read_text(encoding="utf-8", errors="ignore"):
            readers.add(path.relative_to(REPO).as_posix())
    assert readers <= {"minimoi_portal/app.py", "minimoi_portal/config.py",
                       "domains/guild/queue_store.py"}, readers


def _ignored(patterns, path):
    return any(fnmatch.fnmatch(path, p.rstrip("/")) or path.startswith(p.rstrip("/") + "/")
               for p in patterns)


def test_dockerignore_excludes_only_the_queue_file():
    lines = [l.strip() for l in (REPO / ".dockerignore").read_text().splitlines()]
    patterns = [l for l in lines if l and not l.startswith("#") and not l.startswith("!")]
    assert "data/guild/build_queue.json" in patterns
    assert _ignored(patterns, "data/guild/build_queue.json")
    assert not _ignored(patterns, "data/guild/experiment_projection.json")
    assert not _ignored(patterns, "data/guild/cos_context.json")
    data_patterns = [p for p in patterns if p.startswith("data")]
    assert data_patterns == ["data/guild/build_queue.json"]
