"""Run only fake commands under a disposable HOME; no Docker or provider IO."""
import os
import subprocess
from pathlib import Path
import pytest

WRAPPER = Path(__file__).resolve().parents[1] / "scripts/operations/run_curator_cron.sh"


@pytest.fixture
def dev_cron(tmp_path):
    project = tmp_path / "Projects/personal-ai-agents"
    (project / "scripts").mkdir(parents=True)
    toggle = project / "scripts/.curator_dev_cron_on"
    toggle.touch()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    scripts = {
        "date": '#!/bin/bash\ncase "$*" in *"+%H"*) echo "${HOUR:-22}";; *) echo 2026-10-16;; esac\n',
        "python": '#!/bin/bash\necho x_pull >> "$CALLS"\nexit "${X_STATUS:-0}"\n',
        "docker": r'''#!/bin/bash
case "$*" in
 *"cron_state"*) echo state >> "$CALLS"; echo "${STATE:-none}"; exit "${STATE_STATUS:-0}";;
 *"curator_rss_v2.py"*) echo rss >> "$CALLS"; exit "${RSS_STATUS:-0}";;
 *"telegram_bot.py --send"*) echo telegram >> "$CALLS"; exit "${TG_STATUS:-0}";;
 *) echo unexpected >> "$CALLS"; exit 99;;
esac
''',
    }
    for name, body in scripts.items():
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    calls = tmp_path / "calls"
    def run(**env):
        calls.write_text("")
        result = subprocess.run(["/bin/bash", str(WRAPPER)], capture_output=True, text=True,
            timeout=10, env={"HOME": str(tmp_path), "PATH": f"{bin_dir}:/usr/bin:/bin",
                             "CALLS": str(calls), **env})
        return result, calls.read_text().splitlines()
    run.toggle = toggle
    return run


def test_failed_generation_never_sends_or_relabels(dev_cron):
    result, calls = dev_cron(RSS_STATUS="1")
    assert result.returncode == 1
    assert calls == ["state", "x_pull", "rss"]


@pytest.mark.parametrize("state,code,expected", [
    ("delivered", 0, ["state"]), ("resolved", 0, ["state"]),
    ("hold", 1, ["state"]), ("unknown", 1, ["state"]),
    ("generated", 0, ["state", "telegram"]),
    ("none", 0, ["state", "x_pull", "rss", "telegram"]),
])
def test_delivery_state_controls_work(dev_cron, state, code, expected):
    result, calls = dev_cron(STATE=state)
    assert result.returncode == code
    assert calls == expected


@pytest.mark.parametrize("status", ["1", "3", "4"])
def test_failed_or_uncertain_sender_never_claims_success(dev_cron, status):
    result, calls = dev_cron(STATE="generated", TG_STATUS=status)
    assert result.returncode == 1
    assert calls == ["state", "telegram"]
    assert "Briefing complete" not in result.stdout


def test_optional_x_failure_does_not_block_generation(dev_cron):
    result, calls = dev_cron(X_STATUS="1")
    assert result.returncode == 0
    assert calls == ["state", "x_pull", "rss", "telegram"]


def test_disabled_or_outside_window_has_no_effect(dev_cron):
    result, calls = dev_cron(HOUR="21")
    assert result.returncode == 0 and calls == []
    dev_cron.toggle.unlink()
    result, calls = dev_cron()
    assert result.returncode == 0 and calls == []


def test_failed_state_command_is_not_trusted_even_with_valid_stdout(dev_cron):
    result, calls = dev_cron(STATE="none", STATE_STATUS="1")
    assert result.returncode == 1
    assert calls == ["state"]
