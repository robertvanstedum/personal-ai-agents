"""Tests for platform-wide secret naming and legacy compatibility."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from core.get_secret import get_secret


def test_xai_secret_supports_legacy_environment_name(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("GROK_API_KEY", "legacy-environment-key")

    assert get_secret("XAI_API_KEY") == "legacy-environment-key"


def test_xai_secret_supports_legacy_production_ssm_name(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    calls = []

    class FakeSSM:
        def get_parameter(self, Name, WithDecryption):
            calls.append((Name, WithDecryption))
            if Name.endswith("/xai_api_key"):
                raise RuntimeError("canonical parameter not provisioned")
            return {"Parameter": {"Value": "legacy-production-key"}}

    fake_boto3 = SimpleNamespace(client=lambda *args, **kwargs: FakeSSM())
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    assert get_secret("XAI_API_KEY") == "legacy-production-key"
    assert calls == [
        ("/minimoi/production/xai_api_key", True),
        ("/minimoi/production/grok_api_key", True),
    ]
