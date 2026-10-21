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


def test_ack_control_only_records_the_applied_version_and_sends_nothing(tmp_path, capsys):
    (tmp_path / "control.json").write_text(json.dumps({"schema": "mc-standing-control/1", "version": 5,
                                                       "state": "paused", "request_id": None,
                                                       "updated_at": "2026-10-20T19:00:00+00:00"}))
    assert run_cycle.main(args(tmp_path) + ["--ack-control-only"]) == 0
    assert json.loads(capsys.readouterr().out)["applied_state"] == "paused"
    from core.mc_standing.spool import Spool
    ack = Spool(tmp_path / "state").read("cycles", "control-ack")
    assert ack["applied_version"] == 5 and ack["applied_state"] == "paused" and ack["reason"] is None


def _control(tmp_path, state="paused", version=6):
    (tmp_path / "control.json").write_text(json.dumps({"schema": "mc-standing-control/1", "version": version,
                                                       "state": state, "request_id": None,
                                                       "updated_at": "2026-10-20T19:00:00+00:00"}))


def test_ack_only_never_acknowledges_for_an_active_worker(tmp_path, capsys):
    """Codex on b5af0b2f: while a cycle holds the lock, the scheduler must not report the pause
    as applied; the running cycle acknowledges at its own checkpoint."""
    import fcntl
    from core.mc_standing.spool import Spool
    spool = Spool(tmp_path / "state")
    _control(tmp_path)
    fd = os.open(spool.root / ".cycle.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)                       # an active cycle in another process
    try:
        assert run_cycle.main(args(tmp_path) + ["--ack-control-only"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out == {"state": "worker_active", "in_flight": True,
                       "note": "the running cycle acknowledges at its next checkpoint"}
        assert spool.read("cycles", "control-ack") is None
    finally:
        os.close(fd)
    assert run_cycle.main(args(tmp_path) + ["--ack-control-only"]) == 0     # idle now
    assert json.loads(capsys.readouterr().out)["in_flight"] is False
    assert spool.read("cycles", "control-ack")["applied_version"] == 6


def test_running_cycle_marks_itself_in_flight_and_acknowledges_at_its_checkpoint(tmp_path):
    from core.mc_standing import runner
    from core.mc_standing.control import Control
    from core.mc_standing.spool import Spool
    from tests.mc_standing.test_spool_findings_runner import NOW, FakeIngress, _status, _watchlist
    spool = Spool(tmp_path / "state")
    _status(tmp_path / "src")
    _control(tmp_path, state="paused", version=9)
    control = Control(tmp_path / "control.json", tmp_path / "DISABLED", spool)
    seen = []

    def paused():
        seen.append(spool.read("cycles", "worker-status")["state"])
        return control.paused()
    res = runner.run_cycle(spool=spool, watchlist=_watchlist(), policy_id="p0", environment="prod", now=NOW,
                           source_root=tmp_path / "src", ingress=FakeIngress(),
                           destination_binding="owner:mc-operations", kill=control.killed, paused=paused)
    assert res["state"] == "paused" and seen and set(seen) == {"running"}
    assert spool.read("cycles", "control-ack")["applied_version"] == 9
    assert spool.read("cycles", "worker-status")["state"] == "idle"


def test_telegram_copy_needs_both_token_and_chat_binding(tmp_path, capsys):
    a = args(tmp_path, **{"--environment": "dev"}) + ["--dev-fixture", "--telegram-copy"]
    assert run_cycle.main(a) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "telegram_copy_needs_token_and_chat_binding"


def test_telegram_chat_binding_must_be_private_and_numeric(tmp_path):
    chat = tmp_path / "chat"
    chat.write_text("12345")
    os.chmod(chat, 0o644)
    with pytest.raises(SystemExit):
        run_cycle._chat_binding(chat)
    os.chmod(chat, 0o600)
    assert run_cycle._chat_binding(chat) == "12345"
    chat.write_text("@someone")
    with pytest.raises(SystemExit):
        run_cycle._chat_binding(chat)


def test_approved_watchlist_matches_the_candidate_except_approval():
    """Robert approved the candidate as is (20 Oct 2026): only the approval fields differ."""
    import json as _json
    cand = _json.loads((REPO / "config/mc_standing/watchlist.candidate.json").read_text())
    appr = _json.loads((REPO / "config/mc_standing/watchlist.json").read_text())
    strip = lambda d: [{k: v for k, v in w.items() if k not in ("approved_by", "approval_ref")} for w in d["watches"]]
    assert strip(cand) == strip(appr)
    wl = registry.load(REPO / "config/mc_standing/watchlist.json")
    assert all(w.approved_by == "Robert" and not w.approval_ref.startswith("candidate") for w in wl.watches)


def test_approved_watchlist_is_accepted_for_production(tmp_path, capsys):
    a = args(tmp_path, **{"--watchlist": str(REPO / "config/mc_standing/watchlist.json")})
    assert run_cycle.main(a) == 0                          # no control document: paused fail-safe
    assert json.loads(capsys.readouterr().out)["state"] == "paused"
