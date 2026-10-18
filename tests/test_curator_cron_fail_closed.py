"""The EC2 Curator cron keeps generation and delivery apart and fails closed (#305).

The wrapper reads today's state first: delivered, hold, generated or none. It
generates only on "none", stops when curation fails, delivers only through the
strict sender, and never resends a held (started/partial/uncertain) day. These
tests run the real wrapper with fake `docker` and `date` on PATH: no container,
provider, Telegram or network.
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "run_curator_cron_ec2.sh"

FAKE_DOCKER = r"""#!/bin/bash
args="$*"
case "$args" in
  *"from utils.role import role_label"*) echo role >> "$FAKE/calls"; echo production; exit 0 ;;
  *"delivery_ledger import cron_state"*) echo state >> "$FAKE/calls"; printf '%s\n' "${FAKE_STATE-none}"; exit "${FAKE_STATE_EXIT:-0}" ;;
  *"scripts.x.x_pull_incremental"*) echo x_pull >> "$FAKE/calls"; exit "${FAKE_X:-0}" ;;
  *"curator_rss_v2.py"*) echo rss >> "$FAKE/calls"; exit "${FAKE_RSS:-0}" ;;
  *"telegram_bot.py --send"*) echo telegram >> "$FAKE/calls"; exit "${FAKE_TG:-0}" ;;
  *) echo "unexpected: $args" >> "$FAKE/calls"; exit 99 ;;
esac
"""

FAKE_DATE = r"""#!/bin/bash
case "$*" in
  *"+%H"*) echo 13 ;;
  *"+%Y-%m-%d"*) echo 2026-10-16 ;;
  *) echo "Fri Oct 16 13:00:00 UTC 2026" ;;
esac
"""


def _run(tmp_path, **status):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("docker", FAKE_DOCKER), ("date", FAKE_DATE)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    calls = tmp_path / "calls"
    calls.write_text("")
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE": str(tmp_path),
           **{key: str(value) for key, value in status.items()}}
    result = subprocess.run(["bash", str(WRAPPER)], env=env, capture_output=True, text=True, timeout=30)
    return result, calls.read_text().split()


@pytest.mark.parametrize("rss_status", [1, 2, 137])
def test_failed_curation_stops_before_telegram(tmp_path, rss_status):
    result, calls = _run(tmp_path, FAKE_RSS=rss_status)
    assert result.returncode == 1
    assert calls == ["role", "state", "x_pull", "rss"]
    assert f"curator_rss_v2.py exited with status {rss_status}" in result.stdout


def test_fresh_day_generates_then_delivers(tmp_path):
    result, calls = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["role", "state", "x_pull", "rss", "telegram"]


def test_x_pull_failure_still_continues_to_curation(tmp_path):
    result, calls = _run(tmp_path, FAKE_X=1)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["role", "state", "x_pull", "rss", "telegram"]
    assert "x_pull_incremental failed — continuing" in result.stdout


def test_generated_but_undelivered_day_retries_delivery_without_rescoring(tmp_path):
    result, calls = _run(tmp_path, FAKE_STATE="generated")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["role", "state", "telegram"]


def test_delivered_day_runs_nothing(tmp_path):
    result, calls = _run(tmp_path, FAKE_STATE="delivered")
    assert result.returncode == 0
    assert calls == ["role", "state"]


def test_held_day_never_resends(tmp_path):
    result, calls = _run(tmp_path, FAKE_STATE="hold")
    assert result.returncode == 1
    assert calls == ["role", "state"]
    assert "HOLD" in result.stdout


@pytest.mark.parametrize("state", ["", "garbage"])
def test_unreadable_state_runs_nothing(tmp_path, state):
    result, calls = _run(tmp_path, FAKE_STATE=state)
    assert result.returncode == 1
    assert calls == ["role", "state"]


def test_definite_non_delivery_exits_for_a_delivery_retry(tmp_path):
    result, calls = _run(tmp_path, FAKE_TG=1)
    assert result.returncode == 1
    assert "the next hourly run retries delivery" in result.stdout


def test_busy_delivery_lock_sends_nothing_and_exits(tmp_path):
    result, _calls = _run(tmp_path, FAKE_TG=4)
    assert result.returncode == 1 and "BUSY" in result.stdout


@pytest.mark.parametrize("tg_status", [3, 2])
def test_uncertain_delivery_is_held_not_retried(tmp_path, tg_status):
    result, _calls = _run(tmp_path, FAKE_TG=tg_status)
    assert result.returncode == 1
    assert "HOLD" in result.stdout and "not resending" in result.stdout


def test_wrapper_no_longer_stamps_briefing_date():
    assert "data[0]['briefing_date'] = " not in WRAPPER.read_text()
