"""The EC2 Curator cron fails closed when curation fails (#305).

The wrapper used to ignore curator_rss_v2.py's exit status: after a failed
curation it still ran the Telegram send and, when that exited 0, stamped today's
briefing_date onto whatever curator_latest.json held, which also stops the
hourly retry.
These tests run the real wrapper with fake `docker` and `date` on PATH: no
container, provider, Telegram or network.
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "run_curator_cron_ec2.sh"

# Classifies each `docker exec` by what it runs, logs it, and exits with the
# status the test chose for that class.
FAKE_DOCKER = r"""#!/bin/bash
args="$*"
case "$args" in
  *"from utils.role import role_label"*) echo role >> "$FAKE/calls"; echo production; exit 0 ;;
  *"briefing_date', '')[:10]"*) echo file_date >> "$FAKE/calls"; echo "${FAKE_FILE_DATE:-}"; exit 0 ;;
  *"scripts.x.x_pull_incremental"*) echo x_pull >> "$FAKE/calls"; exit "${FAKE_X:-0}" ;;
  *"curator_rss_v2.py"*) echo rss >> "$FAKE/calls"; exit "${FAKE_RSS:-0}" ;;
  *"telegram_bot.py --send"*) echo telegram >> "$FAKE/calls"; exit "${FAKE_TG:-0}" ;;
  *"data[0]['briefing_date'] = "*) echo stamp >> "$FAKE/calls"; exit 0 ;;
  *) echo "unexpected: $args" >> "$FAKE/calls"; exit 99 ;;
esac
"""

# Inside the 12:00-20:00 UTC window; a fixed day for the idempotency check.
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


def test_failed_curation_stops_before_telegram_and_the_briefing_stamp(tmp_path):
    result, calls = _run(tmp_path, FAKE_RSS=1)
    assert result.returncode == 1
    assert calls == ["role", "file_date", "x_pull", "rss"]
    assert "curator_rss_v2.py exited with status 1" in result.stdout
    assert "not stamping briefing_date" in result.stdout


@pytest.mark.parametrize("rss_status", [2, 137])
def test_any_nonzero_curation_status_fails_closed(tmp_path, rss_status):
    result, calls = _run(tmp_path, FAKE_RSS=rss_status)
    assert result.returncode == 1
    assert "telegram" not in calls and "stamp" not in calls


def test_successful_run_sends_then_stamps(tmp_path):
    result, calls = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["role", "file_date", "x_pull", "rss", "telegram", "stamp"]


def test_x_pull_failure_still_continues_to_curation(tmp_path):
    result, calls = _run(tmp_path, FAKE_X=1)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["role", "file_date", "x_pull", "rss", "telegram", "stamp"]
    assert "x_pull_incremental failed — continuing" in result.stdout


def test_telegram_failure_still_skips_the_stamp(tmp_path):
    result, calls = _run(tmp_path, FAKE_TG=1)
    assert result.returncode == 1
    assert calls == ["role", "file_date", "x_pull", "rss", "telegram"]


def test_a_briefing_already_stamped_today_runs_nothing(tmp_path):
    result, calls = _run(tmp_path, FAKE_FILE_DATE="2026-10-16")
    assert result.returncode == 0
    assert calls == ["role", "file_date"]
