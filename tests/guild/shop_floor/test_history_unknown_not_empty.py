"""Item history is read from guild.design_log_transitions, or it is unknown;
a failed read is never an empty history (S11; unlike the legacy endpoint)."""
from __future__ import annotations

from datetime import datetime, timezone

from minimoi_portal.guild_ui.adapters import DbHistory
from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.params = params

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False

    def cursor(self):
        return _Cursor(self.rows)

    def close(self):
        self.closed = True


def test_no_database_is_not_instrumented():
    res = DbHistory(lambda: None).history(12)
    assert res.source == "not_instrumented" and res.status == "unknown" and res.data is None


def test_a_failed_read_is_unknown_not_empty():
    def fail(url):
        raise OSError("connection refused")
    res = DbHistory(lambda: "postgresql://x", connect=fail).history(12)
    assert res.status == "unknown" and res.data is None and res.reason == "read_failed"


def test_rows_are_read_and_an_empty_history_is_a_real_empty():
    at = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
    res = DbHistory(lambda: "u", connect=lambda url: _Conn([("in_build", "done", "robert", "", at)])).history(12)
    assert res.ok and res.data == [{"from": "in_build", "to": "done", "by": "robert", "reason": "", "at": at.isoformat()}]
    empty = DbHistory(lambda: "u", connect=lambda url: _Conn([])).history(12)
    assert empty.ok and empty.data == []


def test_the_api_and_page_say_unknown_on_failure(staging, monkeypatch):
    services = staging.app.extensions["guild_ui_next"]["services"]
    monkeypatch.setattr(services.history, "_database_url", lambda: "postgresql://nobody@127.0.0.1:1/x")

    def fail(url):
        raise OSError("refused")
    monkeypatch.setattr(services.history, "_connect", fail)
    client = staging.owner()
    body = client.get("/guild-next/api/v1/queue/items/12/history").get_json()
    assert body["status"] == "unknown" and body["history"] is None
    page = client.get("/guild-next/guild/build/items/12").get_data(as_text=True)
    assert "treat as unknown" in page and "No transitions recorded" not in page
