"""
tests/test_realtime_voice_duration_guard.py — warning and hard-stop
behavior.

Per _working/CLAUDE_CODE_BUILD_SPEC_voice_realtime_2026-07-24.md Section 12
"Initial session safeguard": VOICE_SESSION_WARNING_MINUTES=20,
VOICE_SESSION_MAX_MINUTES=30 by default, both configuration (env), not
code constants. These deliberately replace an earlier, too-restrictive
10/15-minute proposal.
"""
import pytest

from core.realtime_voice.duration_guard import DurationGuard


def test_defaults_are_20_and_30_minutes(monkeypatch):
    monkeypatch.delenv("VOICE_SESSION_WARNING_MINUTES", raising=False)
    monkeypatch.delenv("VOICE_SESSION_MAX_MINUTES", raising=False)
    guard = DurationGuard()
    assert guard.warning_minutes == 20
    assert guard.max_minutes == 30


def test_defaults_are_not_the_old_10_and_15_minute_proposal(monkeypatch):
    monkeypatch.delenv("VOICE_SESSION_WARNING_MINUTES", raising=False)
    monkeypatch.delenv("VOICE_SESSION_MAX_MINUTES", raising=False)
    guard = DurationGuard()
    assert guard.warning_minutes != 10
    assert guard.max_minutes != 15


def test_env_vars_override_defaults(monkeypatch):
    monkeypatch.setenv("VOICE_SESSION_WARNING_MINUTES", "15")
    monkeypatch.setenv("VOICE_SESSION_MAX_MINUTES", "25")
    guard = DurationGuard()
    assert guard.warning_minutes == 15
    assert guard.max_minutes == 25


def test_status_before_warning_threshold():
    guard = DurationGuard(warning_minutes=20, max_minutes=30)
    assert guard.status(elapsed_seconds=5 * 60) == "ok"


def test_status_at_warning_threshold():
    guard = DurationGuard(warning_minutes=20, max_minutes=30)
    assert guard.status(elapsed_seconds=20 * 60) == "warning"


def test_status_between_warning_and_max():
    guard = DurationGuard(warning_minutes=20, max_minutes=30)
    assert guard.status(elapsed_seconds=25 * 60) == "warning"


def test_status_at_max_is_hard_stop():
    guard = DurationGuard(warning_minutes=20, max_minutes=30)
    assert guard.status(elapsed_seconds=30 * 60) == "stop"


def test_status_past_max_is_still_hard_stop():
    guard = DurationGuard(warning_minutes=20, max_minutes=30)
    assert guard.status(elapsed_seconds=45 * 60) == "stop"


def test_invalid_config_where_warning_exceeds_max_raises():
    with pytest.raises(ValueError):
        DurationGuard(warning_minutes=30, max_minutes=20)
