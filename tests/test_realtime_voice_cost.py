"""
tests/test_realtime_voice_cost.py — realtime voice sessions log through the
shared cost path (utils/cost_log.py), not a separate voice-only logger.

Per _working/CLAUDE_CODE_BUILD_SPEC_voice_realtime_2026-07-24.md Section 12:
"Use the already-approved cross-domain cost design ... Do not create a
separate voice-only cost file or logger." "Do not infer exact billed cost
from wall-clock time when provider usage is available."
"""
import json

import pytest

from core.realtime_voice.cost import log_realtime_voice_cost
from utils.cost_log import resolve_log_path


@pytest.fixture
def tmp_log_path(tmp_path, monkeypatch):
    log_path = tmp_path / "costs.json"
    monkeypatch.setitem(resolve_log_path.__globals__["_LOG_PATHS"], "german", log_path)
    return log_path


def test_uses_shared_cost_log_path_not_a_separate_file(tmp_log_path):
    log_realtime_voice_cost(
        domain="german",
        provider="xai",
        model="grok-voice-agent",
        session_id="sess-1",
        duration_seconds=120,
        provider_usage={"session_seconds": 120},
        cost_usd=0.10,
    )
    assert tmp_log_path.exists()
    runs = json.loads(tmp_log_path.read_text())["runs"]
    assert len(runs) == 1
    assert runs[0]["use_type"] == "realtime_voice"
    assert runs[0]["cost_basis"] == "provider_reported"
    assert runs[0]["session_id"] == "sess-1"


def test_falls_back_to_pricing_estimate_when_no_provider_cost_given(tmp_log_path):
    log_realtime_voice_cost(
        domain="german",
        provider="xai",
        model="grok-voice-agent",
        session_id="sess-2",
        duration_seconds=60,
    )
    runs = json.loads(tmp_log_path.read_text())["runs"]
    assert runs[0]["cost_basis"] == "estimated"
    assert runs[0]["cost_usd"] > 0


def test_openai_without_provider_cost_raises_rather_than_guess(tmp_log_path):
    # gpt-realtime-2 has no verified per-minute PRICING rate -- an
    # unverified guess is worse than a required, honest failure here.
    with pytest.raises(ValueError):
        log_realtime_voice_cost(
            domain="german",
            provider="openai",
            model="gpt-realtime-2",
            session_id="sess-3",
            duration_seconds=60,
        )
