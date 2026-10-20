"""The standing entry point refuses unapproved watchlists and loose tokens."""
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "mc_standing"))
import run_cycle  # noqa: E402

from core.mc_standing import registry


def args(tmp_path, **over):
    token = tmp_path / "token"
    token.write_text("t" * 40)
    os.chmod(token, 0o600)
    base = {"--watchlist": str(REPO / "config/mc_standing/watchlist.candidate.json"),
            "--state-root": str(tmp_path / "state"), "--source-root": str(tmp_path / "src"),
            "--environment": "prod", "--policy-id": "p0", "--destination": "owner:mc-operations",
            "--ingress-url": "http://127.0.0.1:9", "--token-file": str(token),
            "--control-file": str(tmp_path / "control.json"), "--disable-file": str(tmp_path / "DISABLED")}
    base.update(over)
    return [x for kv in base.items() for x in kv]


def test_candidate_watchlist_parses_and_covers_five_watch_areas():
    wl = registry.load(REPO / "config/mc_standing/watchlist.candidate.json")
    adapters = {w.adapter_id for w in wl.watches}
    assert adapters == {"job_status", "health_snapshot", "host_snapshot", "release_status", "guest_summary"}
    assert all(w.approval_ref.startswith("candidate") for w in wl.watches)


def test_candidate_watchlist_is_refused_in_production(tmp_path, capsys):
    assert run_cycle.main(args(tmp_path)) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "watchlist_not_approved"


def test_dev_fixture_flag_only_in_dev(tmp_path, capsys):
    assert run_cycle.main(args(tmp_path) + ["--dev-fixture"]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "dev_fixture_only_in_dev"


def test_world_readable_token_is_refused(tmp_path):
    a = args(tmp_path, **{"--environment": "dev"}) + ["--dev-fixture"]
    os.chmod(tmp_path / "token", 0o644)
    with pytest.raises(SystemExit):
        run_cycle.main(a)


def test_dev_fixture_run_without_control_is_paused_fail_safe(tmp_path, capsys):
    a = args(tmp_path, **{"--environment": "dev"}) + ["--dev-fixture"]
    # dev run against the prod-scoped candidate: environment mismatch makes every watch unknown,
    # but with no control document the cycle is paused first, and nothing is sent.
    assert run_cycle.main(a) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "paused"
