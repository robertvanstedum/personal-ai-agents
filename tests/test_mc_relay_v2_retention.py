"""Relay v2 keeps every job and tombstone record until an explicit reviewed disposition: the 15 Oct 2026 reviewed
retention change (operator record minimoi-12-push-20261015/relay-migration-v5) removed the seven-day automatic expiry.
Runs that review's own five checks against docker/mc-agent/relay_v2.mjs."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RELAY = REPO / "docker" / "mc-agent" / "relay_v2.mjs"
CHECK = REPO / "tests" / "fixtures" / "relay_v2_retention_check.mjs"


def test_no_automatic_job_expiry_in_the_source():
    source = RELAY.read_text(encoding="utf-8")
    assert "KEEP_JOBS_MS" not in source


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_load_jobs_retains_audit_records():
    out = subprocess.run(["node", str(CHECK), str(RELAY)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "5 retention checks passed" in out.stdout
