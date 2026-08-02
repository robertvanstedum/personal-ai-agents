"""
tests/test_realtime_voice_config.py — provider precedence and allow-listing.

Per _working/CLAUDE_CODE_BUILD_SPEC_voice_realtime_2026-07-24.md Section 7:
precedence is explicit selection > saved per-user preference > env default >
application default. Section 6/16: production must reject a query-string
provider override; a dev-only override may exist server-side.
"""
import pytest

from core.realtime_voice.config import (
    ALLOWED_PROVIDERS,
    ALLOWED_LOCALES,
    resolve_provider,
    InvalidProviderError,
)


def test_allowed_providers_are_openai_and_xai_only():
    # Claude has no public realtime voice API (investigation finding,
    # 2026-07-24) -- not part of the realtime provider toggle.
    assert ALLOWED_PROVIDERS == frozenset({"openai", "xai"})


def test_explicit_selection_wins_over_everything(monkeypatch):
    monkeypatch.setenv("VOICE_REALTIME_PROVIDER_DEFAULT", "xai")
    result = resolve_provider(
        explicit="openai", saved_preference="xai", is_production=True
    )
    assert result == "openai"


def test_saved_preference_wins_over_env_default(monkeypatch):
    monkeypatch.setenv("VOICE_REALTIME_PROVIDER_DEFAULT", "xai")
    result = resolve_provider(
        explicit=None, saved_preference="openai", is_production=True
    )
    assert result == "openai"


def test_env_default_wins_over_application_default(monkeypatch):
    monkeypatch.setenv("VOICE_REALTIME_PROVIDER_DEFAULT", "xai")
    result = resolve_provider(
        explicit=None, saved_preference=None, is_production=True
    )
    assert result == "xai"


def test_application_default_used_when_nothing_else_set(monkeypatch):
    monkeypatch.delenv("VOICE_REALTIME_PROVIDER_DEFAULT", raising=False)
    result = resolve_provider(
        explicit=None, saved_preference=None, is_production=True
    )
    assert result in ALLOWED_PROVIDERS  # a real, defined application default


def test_application_default_is_specifically_openai(monkeypatch):
    """Pinned per the German dev-drift fix (2026-08-02): the dev-drift
    report specifically calls out 'OpenAI Realtime default' as part of
    what production already has and dev must match. The looser
    'in ALLOWED_PROVIDERS' check above would still pass if this were ever
    accidentally flipped to xai."""
    monkeypatch.delenv("VOICE_REALTIME_PROVIDER_DEFAULT", raising=False)
    result = resolve_provider(
        explicit=None, saved_preference=None, is_production=True
    )
    assert result == "openai"


def test_invalid_explicit_provider_rejected():
    with pytest.raises(InvalidProviderError):
        resolve_provider(explicit="claude", saved_preference=None, is_production=True)


def test_invalid_explicit_provider_rejected_even_if_not_empty_string():
    with pytest.raises(InvalidProviderError):
        resolve_provider(explicit="not-a-provider", saved_preference=None, is_production=False)


def test_invalid_saved_preference_falls_through_rather_than_raising(monkeypatch):
    # A corrupted/stale saved preference must not break session start --
    # fail open to the next precedence tier, not fail closed with an error.
    monkeypatch.setenv("VOICE_REALTIME_PROVIDER_DEFAULT", "openai")
    result = resolve_provider(
        explicit=None, saved_preference="not-a-provider", is_production=True
    )
    assert result == "openai"


def test_dev_query_string_override_allowed_when_not_production(monkeypatch):
    monkeypatch.delenv("VOICE_REALTIME_PROVIDER_DEFAULT", raising=False)
    result = resolve_provider(
        explicit=None,
        saved_preference=None,
        is_production=False,
        dev_query_override="xai",
    )
    assert result == "xai"


def test_query_string_override_rejected_in_production(monkeypatch):
    # Section 6/16: "Do not expose a production query-string provider
    # override." A dev override value must be silently ignored in prod, not
    # honored and not raise -- the request should proceed on the normal
    # precedence chain as if no override were given.
    monkeypatch.setenv("VOICE_REALTIME_PROVIDER_DEFAULT", "openai")
    result = resolve_provider(
        explicit=None,
        saved_preference=None,
        is_production=True,
        dev_query_override="xai",
    )
    assert result == "openai"


def test_locale_allow_list_covers_both_domains():
    assert ALLOWED_LOCALES == frozenset({"de-AT", "pt-BR"})
