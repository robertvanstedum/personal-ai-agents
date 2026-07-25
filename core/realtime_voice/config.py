"""
core/realtime_voice/config.py — provider precedence, allow-listing, and
locale allow-listing for the shared realtime voice module.

Per _working/CLAUDE_CODE_BUILD_SPEC_voice_realtime_2026-07-24.md Section 7:

    1. explicit selection in the session-start interface
    2. saved per-user preference, if implemented
    3. VOICE_REALTIME_PROVIDER_DEFAULT
    4. application default

Claude is deliberately not in ALLOWED_PROVIDERS -- no public realtime voice
API exists for it (investigation finding, 2026-07-24); it keeps its
existing role as the post-session review model, untouched by this module.
"""
import os

ALLOWED_PROVIDERS = frozenset({"openai", "xai"})
ALLOWED_LOCALES = frozenset({"de-AT", "pt-BR"})

_APPLICATION_DEFAULT_PROVIDER = "openai"


class InvalidProviderError(ValueError):
    pass


def resolve_provider(
    *,
    explicit: str | None,
    saved_preference: str | None,
    is_production: bool,
    dev_query_override: str | None = None,
) -> str:
    """Resolve which provider a session should use.

    `explicit` (from the session-start interface) is validated strictly --
    an invalid explicit choice raises, since the user made a concrete,
    wrong request. `saved_preference` and `dev_query_override` fail open
    (silently ignored if invalid/not-allowed-in-context) rather than
    raising, since they're inferred/ambient inputs, not a direct request --
    a stale or malicious value there should not break session start.
    """
    if explicit is not None:
        if explicit not in ALLOWED_PROVIDERS:
            raise InvalidProviderError(
                f"Unknown provider: {explicit!r}. Allowed: {sorted(ALLOWED_PROVIDERS)}"
            )
        return explicit

    if not is_production and dev_query_override in ALLOWED_PROVIDERS:
        return dev_query_override

    if saved_preference in ALLOWED_PROVIDERS:
        return saved_preference

    env_default = os.environ.get("VOICE_REALTIME_PROVIDER_DEFAULT")
    if env_default in ALLOWED_PROVIDERS:
        return env_default

    return _APPLICATION_DEFAULT_PROVIDER
