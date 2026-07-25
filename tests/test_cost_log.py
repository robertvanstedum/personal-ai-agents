"""
tests/test_cost_log.py — utils/cost_log.py contract tests.

Covers the approved design in docs/specs/spec_ai_cost_tracking_2026-07-19.md
(token-based path) plus the duration/audio-metered extension required by
_working/CLAUDE_CODE_BUILD_SPEC_voice_realtime_2026-07-24.md Section 12.

Scope discipline: these tests exercise the shared utils/cost_log.py module
itself. They do not wire or exercise Curator/CoS/legacy German-Portuguese
text-chat call sites -- that retrofit is explicitly out of scope for the
realtime-voice build (Robert, 2026-07-24).
"""
import json

import pytest

from utils.cost_log import log_ai_cost, resolve_log_path, PRICING


@pytest.fixture
def tmp_log_path(tmp_path, monkeypatch):
    log_path = tmp_path / "costs.json"
    monkeypatch.setitem(resolve_log_path.__globals__["_LOG_PATHS"], "german", log_path)
    return log_path


@pytest.fixture
def token_priced_model(monkeypatch):
    """A test-only token-priced model, so the general token-based path can be
    tested without shipping pricing for a real legacy-call-site model this
    build does not wire (scope discipline, see module docstring)."""
    monkeypatch.setitem(PRICING, "test-token-model", {"input": 0.00001, "output": 0.00003})
    return "test-token-model"


def _read(log_path):
    return json.loads(log_path.read_text())["runs"]


# ── Token-based path (original approved contract, preserved) ────────────────

def test_token_based_call_computes_cost_from_pricing_table(tmp_log_path, token_priced_model):
    log_ai_cost(
        domain="german",
        model=token_priced_model,
        use_type="chat_turn",
        input_tokens=1000,
        output_tokens=500,
    )
    runs = _read(tmp_log_path)
    assert len(runs) == 1
    entry = runs[0]
    assert entry["model"] == token_priced_model
    assert entry["use_type"] == "chat_turn"
    assert entry["input_tokens"] == 1000
    assert entry["output_tokens"] == 500
    assert entry["cost_usd"] == pytest.approx(1000 * 0.00001 + 500 * 0.00003)
    assert entry["cost_basis"] == "estimated"
    assert entry["pricing_table_version"] == PRICING_VERSION_FOR_TEST()


def test_token_based_call_with_unknown_model_raises(tmp_log_path):
    with pytest.raises(KeyError):
        log_ai_cost(
            domain="german",
            model="not-a-real-model",
            use_type="chat_turn",
            input_tokens=100,
            output_tokens=50,
        )


# ── Duration/audio-metered path (new, for realtime voice) ───────────────────

def test_duration_metered_call_with_explicit_provider_cost_uses_it_directly(tmp_log_path):
    log_ai_cost(
        domain="german",
        model="grok-voice-agent",
        use_type="realtime_voice",
        duration_seconds=182.4,
        cost_usd=0.1520,
        provider_usage={"session_seconds": 182.4},
        session_id="sess-abc123",
    )
    runs = _read(tmp_log_path)
    entry = runs[0]
    assert entry["duration_seconds"] == 182.4
    assert entry["cost_usd"] == 0.1520
    assert entry["cost_basis"] == "provider_reported"
    assert "pricing_table_version" not in entry
    assert entry["provider_usage"] == {"session_seconds": 182.4}
    assert entry["session_id"] == "sess-abc123"


def test_duration_metered_call_without_provider_cost_estimates_from_pricing(tmp_log_path):
    log_ai_cost(
        domain="german",
        model="grok-voice-agent",
        use_type="realtime_voice",
        duration_seconds=120,
    )
    runs = _read(tmp_log_path)
    entry = runs[0]
    assert entry["cost_basis"] == "estimated"
    assert entry["cost_usd"] == pytest.approx(PRICING["grok-voice-agent"]["per_minute"] * 2)


def test_duration_metered_call_for_model_without_pricing_and_no_cost_raises(tmp_log_path):
    with pytest.raises(ValueError):
        log_ai_cost(
            domain="german",
            model="gpt-realtime-2",
            use_type="realtime_voice",
            duration_seconds=60,
        )


def test_call_with_neither_tokens_duration_nor_cost_raises(tmp_log_path):
    with pytest.raises(ValueError):
        log_ai_cost(domain="german", model="gpt-4o-mini", use_type="chat_turn")


# ── Per-domain file routing ──────────────────────────────────────────────────

def test_unknown_domain_raises(tmp_log_path):
    with pytest.raises(KeyError):
        log_ai_cost(
            domain="not-a-domain",
            model="gpt-4o-mini",
            use_type="chat_turn",
            input_tokens=10,
            output_tokens=10,
        )


def test_multiple_calls_append_not_overwrite(tmp_log_path):
    log_ai_cost(domain="german", model="whisper-1", use_type="transcribe",
                duration_seconds=30)
    log_ai_cost(domain="german", model="whisper-1", use_type="transcribe",
                duration_seconds=45)
    runs = _read(tmp_log_path)
    assert len(runs) == 2


def test_record_has_domain_date_and_timestamp(tmp_log_path):
    log_ai_cost(domain="german", model="whisper-1", use_type="transcribe",
                duration_seconds=30)
    entry = _read(tmp_log_path)[0]
    assert entry["domain"] == "german"
    assert "date" in entry
    assert "timestamp" in entry


def PRICING_VERSION_FOR_TEST():
    from utils.cost_log import PRICING_VERSION
    return PRICING_VERSION
