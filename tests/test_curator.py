import sys

import pytest

from domains.curator import curator_rss_v2
from domains.curator.curator_rss_v2 import (
    XAI_RANKING_MODEL,
    calculate_haiku_cost,
    calculate_xai_cost,
    resolve_scoring_mode,
)


def test_curator_health(curator_client):
    r = curator_client.get("/health")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "ok"


def test_curator_daily_loads(curator_client):
    r = curator_client.get("/")
    assert r.status_code in [200, 302]


def test_curator_production_model_is_explicit_and_supported():
    assert XAI_RANKING_MODEL == "grok-4.3"
    assert resolve_scoring_mode("grok-4.3") == "xai"
    assert resolve_scoring_mode("xai") == "xai"


def test_curator_rejects_unknown_model_instead_of_silent_fallback():
    with pytest.raises(ValueError, match="Unsupported Curator model"):
        resolve_scoring_mode("grok-typo")


def test_curator_xai_cost_uses_current_grok_43_rates():
    # 1M input + 1M output at $1.25/$2.50 per million.
    assert calculate_xai_cost(1_000_000, 1_000_000) == pytest.approx(3.75)
    # Representative recent Curator run: about nine cents, not the stale $0.39.
    assert calculate_xai_cost(65_000, 4_096) == pytest.approx(0.09149)


def test_curator_haiku_cost_uses_current_45_rates():
    assert calculate_haiku_cost(1_000_000, 1_000_000) == pytest.approx(6.00)


def test_curator_xai_key_uses_shared_platform_secret_helper(monkeypatch):
    calls = {}

    def fake_get_secret(*args):
        calls["args"] = args
        return "xai-test-key"

    monkeypatch.setattr(curator_rss_v2, "get_secret", fake_get_secret)

    assert curator_rss_v2.get_xai_api_key() == "xai-test-key"
    assert calls["args"] == ("XAI_API_KEY", "xai", "api_key")


def test_curator_xai_scorer_calls_platform_key_resolver(monkeypatch):
    calls = {"count": 0}

    def unavailable_key():
        calls["count"] += 1
        raise RuntimeError("test credential unavailable")

    monkeypatch.setattr(curator_rss_v2, "get_xai_api_key", unavailable_key)
    # The mechanical fallback must remain usable even when the optional
    # OpenAI-compatible client is not installed in the environment.
    monkeypatch.setitem(sys.modules, "openai", None)

    assert curator_rss_v2.score_entries_xai([], fallback_on_error=True) == []
    assert calls["count"] == 1
