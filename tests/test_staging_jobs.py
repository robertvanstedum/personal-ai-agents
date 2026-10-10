"""The scheduled-jobs status folder on staging: docker-compose.staging-jobs.yml gives the portal MINIMOI_JOBS_DIR
and a READ-ONLY mount of data/jobs. Opt-in (nothing in scripts/staging includes it yet; Codex owns those); production
never sets it. Also the classifier rules that keep the Mac jobs from triggering a production deploy."""
from __future__ import annotations

from pathlib import Path

import yaml

from test_staging_environment import PROD, STAGING, _compose_config, _load  # noqa: F401

REPO = Path(__file__).resolve().parent.parent
OVERLAY = REPO / "docker-compose.staging-jobs.yml"


def test_the_overlay_adds_only_the_portals_jobs_dir_read_only():
    assert _load(OVERLAY) == {"services": {"portal": {
        "environment": ["MINIMOI_JOBS_DIR=/app/data/jobs"],
        "volumes": ["${MINIMOI_ROOT:?}/data/jobs:/app/data/jobs:ro"],
    }}}
    extra = {"MINIMOI_ROOT": "/Users/x/minimoi-staging", "MINIMOI_IMAGE_TAG": "abc1234"}
    with_jobs = yaml.safe_load(_compose_config(PROD, STAGING, OVERLAY, env_extra=extra))["services"]
    without = yaml.safe_load(_compose_config(PROD, STAGING, env_extra=extra))["services"]
    portal = with_jobs["portal"]
    assert portal["environment"]["MINIMOI_JOBS_DIR"] == "/app/data/jobs"
    mount = next(v for v in portal["volumes"] if v["target"] == "/app/data/jobs")
    assert mount["source"] == "/Users/x/minimoi-staging/data/jobs" and mount.get("read_only") is True
    assert {(v["source"], v["target"]) for v in without["portal"]["volumes"]} <= {(v["source"], v["target"]) for v in portal["volumes"]}
    for name in without:
        if name != "portal":
            assert with_jobs[name] == without[name], name
    assert "MINIMOI_JOBS_DIR" not in without["portal"]["environment"]


def test_production_never_sets_the_jobs_dir():
    assert "MINIMOI_JOBS_DIR" not in PROD.read_text()
    assert "MINIMOI_JOBS_DIR" not in (REPO / "docker-compose.staging.yml").read_text()


def test_the_release_classifies_the_overlay_as_staging_only():
    from scripts.ci.classify_release import STAGING_ONLY_FILES, classify
    assert "docker-compose.staging-jobs.yml" in STAGING_ONLY_FILES
    assert classify(["docker-compose.staging-jobs.yml"]) == ("documents", ())


def test_the_mac_jobs_and_their_launchd_files_redeploy_nothing():
    from scripts.ci.classify_release import classify
    for path in ("scripts/memory/daily_watch.py", "scripts/jobs/watchdog.py", "scripts/jobs/launchd.sh",
                 "infrastructure/launchd/com.vanstedum.minimoi-memory-watch.plist", "tests/jobs/test_status.py",
                 "docs/jobs_status_contract.md"):
        assert classify([path]) == ("documents", ()), path


def test_the_registry_and_status_contract_redeploy_only_the_portal():
    from scripts.ci.classify_release import classify
    assert classify(["core/jobs/status.py"]) == ("domain", ("portal",))
    assert classify(["config/scheduled_jobs.json"]) == ("domain", ("portal",))
    assert classify(["core/jobs/judge.py", "scripts/jobs/watchdog.py"]) == ("domain", ("portal",))
    assert "curator" in classify(["core/other_thing.py"])[1]          # other core/ files keep their wider rule
