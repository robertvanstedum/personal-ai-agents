"""A real (SQLite) voice-budget ledger for route tests: MC voice and dictation REQUIRE the budget, so their tests need one. No network."""
from __future__ import annotations

from core.realtime_voice import budget


def install(monkeypatch, tmp_path, *, cap=20.0):
    path = tmp_path / "voice_budget.db"
    monkeypatch.setenv(budget.ENV_DSN, f"sqlite:///{path}")
    monkeypatch.setenv("MINIMOI_LIMITS_DIR", str(tmp_path / "limits"))
    (tmp_path / "limits").mkdir(exist_ok=True)
    if cap != 20.0:
        (tmp_path / "limits" / "limits.json").write_text('{"version": 1, "values": {"voice.daily_cap_usd": %s}}' % cap)
    monkeypatch.setattr(budget, "_default", None)
    b = budget.default_budget()
    b.ensure_schema()
    return b
