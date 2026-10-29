"""CLI gateway totals must agree with the reusable checkpoint contract."""
from datetime import datetime
import hashlib

import pytest

from scripts import cost_report

class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 8, 15, tzinfo=tz)

@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    monkeypatch.setattr(cost_report, "datetime", FixedDateTime)

def receipts(bad=0.0):
    return [
        {"occurred_at": "2026-08-15T10:00:00+00:00", "cost_usd": 2.0, "fallback_position": 0},
        {"occurred_at": "2026-08-15T11:00:00+00:00", "cost_usd": bad, "fallback_position": True},
    ]

def render(period, rows):
    if period == "today":
        cost_report.report_today({}, [], rows)
    elif period == "year":
        cost_report.report_year({}, [], rows)
    else:
        cost_report.report_days({}, [], rows, ["2026-08-14", "2026-08-15"], period)

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 10**400])
@pytest.mark.parametrize("period", ["today", "week", "month", "year"])
def test_invalid_gateway_cost_cannot_erase_valid_report_total(capsys, bad, period):
    render(period, receipts(bad))
    text = capsys.readouterr().out
    assert "$2.00" in text
    assert "$nan" not in text and "$inf" not in text and "$-" not in text
    if period == "today":
        assert "2 request(s), 0 fallback(s)" in text

def test_daily_aggregation_preserves_requests_but_ignores_invalid_cost_and_boolean_fallback():
    assert cost_report.gateway_by_date(receipts(float("nan")))["2026-08-15"] == {
        "cost": 2.0, "requests": 2, "fallbacks": 0,
    }


@pytest.mark.parametrize("period, expected", [
    ("today", "ebf12d7b4861d8339f0f5f32a22b31950f536ffe716b68fcae96c4c3eb2c2d55"),
    ("week", "839e81ffa3438279b1928f81c520106f9040d82f7c4994abfea15c698eef5486"),
    ("month", "e766bf6bfb8f1ddb7158dc2bea2c688b45a7204b80de9cf5b3d3d60c5c61afe9"),
    ("year", "be71280ebaf78ca98a90b985242b9d50a363b9ec2066d0837b47ad215d6e7dee"),
])
def test_valid_report_output_matches_pre_refactor_snapshot(capsys, period, expected):
    rows = [
        {"occurred_at": "2026-08-15T10:00:00+00:00", "cost_usd": 2.5, "fallback_position": 0},
        {"occurred_at": "2026-08-14T10:00:00+00:00", "cost_usd": 0.5, "fallback_position": 1},
    ]
    render(period, rows)
    assert hashlib.sha256(capsys.readouterr().out.encode()).hexdigest() == expected


@pytest.mark.parametrize("period", ["today", "week", "month", "year"])
def test_cli_preserves_subcent_boundary_until_display_rounding(capsys, period):
    rows = [{"occurred_at": "2026-08-15T10:00:00+00:00", "cost_usd": 0.004999999}]
    render(period, rows)
    text = capsys.readouterr().out
    assert "$0.01" not in text
    assert "$0.00" in text

def test_daily_values_keep_precision_for_later_range_totals():
    rows = [
        {"occurred_at": "2026-08-14T10:00:00+00:00", "cost_usd": 0.002499999},
        {"occurred_at": "2026-08-15T10:00:00+00:00", "cost_usd": 0.002499999},
    ]
    days = cost_report.gateway_by_date(rows)
    assert sum(day["cost"] for day in days.values()) == 0.004999998

def test_checkpoint_default_rounding_remains_explicit():
    from services.model_gateway.cost_reporting import summarize_receipts
    rows = [{"occurred_at": "2026-08-15T10:00:00+00:00", "cost_usd": 0.004999999}]
    rounded = summarize_receipts(rows, "2026-08")
    exact = summarize_receipts(rows, "2026-08", round_costs=False)
    assert rounded["cost_usd"] == 0.005
    assert rounded["by_provider"]["unknown"]["cost_usd"] == 0.005
    assert exact["cost_usd"] == 0.004999999
    assert exact["by_provider"]["unknown"]["cost_usd"] == 0.004999999
