"""Focused tests for Curator's shared xAI secret lookup."""

from __future__ import annotations

import pytest

from core import get_secret as secret_module
from domains.curator import curator_rss_v2


def test_get_xai_api_key_uses_shared_secret_resolver(monkeypatch):
    calls = []

    def fake_get_secret(key, service, account):
        calls.append((key, service, account))
        return "test-xai-key"

    monkeypatch.setattr(secret_module, "get_secret", fake_get_secret)

    assert curator_rss_v2.get_xai_api_key() == "test-xai-key"
    assert calls == [("XAI_API_KEY", "xai", "api_key")]


def test_get_xai_model_uses_runtime_configuration(monkeypatch):
    monkeypatch.setenv("CURATOR_XAI_MODEL", "configured-model")

    assert curator_rss_v2.get_xai_model() == "configured-model"


def test_explicit_xai_model_overrides_runtime_configuration(monkeypatch):
    monkeypatch.setenv("CURATOR_XAI_MODEL", "configured-model")

    assert curator_rss_v2.get_xai_model("manual-model") == "manual-model"


def test_get_xai_model_requires_configuration(monkeypatch):
    monkeypatch.delenv("CURATOR_XAI_MODEL", raising=False)

    with pytest.raises(ValueError, match="xAI model not configured"):
        curator_rss_v2.get_xai_model()


def test_score_entries_xai_fails_cleanly_when_secret_is_unavailable(monkeypatch):
    monkeypatch.setattr(curator_rss_v2, "get_xai_api_key", lambda: "")

    with pytest.raises(ValueError, match="xAI API key not found"):
        curator_rss_v2.score_entries_xai([], fallback_on_error=False)


def test_score_entries_xai_can_fall_back_when_secret_is_unavailable(monkeypatch):
    monkeypatch.setattr(curator_rss_v2, "get_xai_api_key", lambda: "")

    assert curator_rss_v2.score_entries_xai([], fallback_on_error=True) == []


def test_score_entries_xai_can_fall_back_when_model_is_unconfigured(monkeypatch):
    monkeypatch.setattr(curator_rss_v2, "get_xai_api_key", lambda: "test-key")
    monkeypatch.delenv("CURATOR_XAI_MODEL", raising=False)

    assert curator_rss_v2.score_entries_xai([], fallback_on_error=True) == []
