"""The existing unified CLI must include gateway cost evidence."""

from datetime import datetime

from scripts import cost_report


def test_gateway_by_date_counts_cost_requests_and_fallbacks():
    totals = cost_report.gateway_by_date([
        {
            "occurred_at": "2026-08-15T10:00:00+00:00",
            "cost_usd": 0.01,
            "fallback_position": 0,
        },
        {
            "occurred_at": "2026-08-15T11:00:00+00:00",
            "cost_usd": 0.02,
            "fallback_position": 2,
        },
    ])

    assert totals["2026-08-15"] == {
        "cost": 0.03,
        "requests": 2,
        "fallbacks": 1,
    }


def test_today_report_names_gateway_as_a_distinct_cost_source(capsys, monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 15, tzinfo=tz)

    monkeypatch.setattr(cost_report, "datetime", FixedDateTime)
    cost_report.report_today(
        {},
        [],
        [{
            "occurred_at": "2026-08-15T10:00:00+00:00",
            "cost_usd": 0.01,
            "fallback_position": 0,
        }],
    )

    output = capsys.readouterr().out
    assert "Model gateway:" in output
    assert "1 request(s), 0 fallback(s)" in output
    assert "Recent days (chat + curator + gateway):" in output
