"""Provider capabilities for the platform-owned voice layer.

Domains select an interaction mode and locale. They do not decide how a
provider transports audio or authenticates a browser connection. Keeping that
contract here prevents provider-specific branches from spreading into German,
Portuguese, or future domain pages.
"""
from dataclasses import asdict, dataclass
import os

from core.realtime_voice.config import ALLOWED_PROVIDERS


MEMO_MODE = "memo"


@dataclass(frozen=True)
class ProviderCapability:
    provider: str
    label: str
    streaming_transcription: bool
    browser_transport: str
    browser_direct_ephemeral_auth: bool
    server_proxy_required: bool
    supported_locales: tuple[str, ...]
    memo_available: bool
    unavailable_reason: str | None = None

    def public_dict(self) -> dict:
        data = asdict(self)
        data["supported_locales"] = list(self.supported_locales)
        return data


_CAPABILITIES = {
    "openai": ProviderCapability(
        provider="openai",
        label="OpenAI",
        streaming_transcription=True,
        browser_transport="webrtc",
        browser_direct_ephemeral_auth=True,
        server_proxy_required=False,
        supported_locales=("de-AT", "pt-BR"),
        memo_available=True,
    ),
    "xai": ProviderCapability(
        provider="xai",
        label="Grok",
        streaming_transcription=True,
        browser_transport="websocket",
        browser_direct_ephemeral_auth=False,
        server_proxy_required=True,
        supported_locales=("de-AT", "pt-BR"),
        memo_available=False,
        unavailable_reason=(
            "xAI streaming STT requires a server-side WebSocket proxy; "
            "browser-direct authentication is not supported"
        ),
    ),
}


def get_provider_capability(provider: str) -> ProviderCapability:
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError(f"Unknown voice provider: {provider!r}")
    return _CAPABILITIES[provider]


def providers_for_mode(mode: str, locale: str, *, available_only: bool = True) -> list[ProviderCapability]:
    if mode != MEMO_MODE:
        raise ValueError(f"Unknown voice mode: {mode!r}")

    providers = [
        capability
        for capability in _CAPABILITIES.values()
        if capability.streaming_transcription
        and locale in capability.supported_locales
        and (capability.memo_available or not available_only)
    ]
    return sorted(providers, key=lambda item: item.provider)


def resolve_memo_provider(requested: str | None, locale: str) -> ProviderCapability:
    available = providers_for_mode(MEMO_MODE, locale)
    available_by_name = {capability.provider: capability for capability in available}

    if requested:
        if requested not in ALLOWED_PROVIDERS:
            raise ValueError(f"Unknown voice provider: {requested!r}")
        if requested not in available_by_name:
            capability = get_provider_capability(requested)
            raise ProviderUnavailableError(
                capability.unavailable_reason or f"{requested} is unavailable for memo mode"
            )
        return available_by_name[requested]

    configured = os.environ.get("VOICE_MEMO_PROVIDER_DEFAULT", "openai")
    if configured in available_by_name:
        return available_by_name[configured]
    if not available:
        raise ProviderUnavailableError("No voice provider is available for memo mode")
    return available[0]


class ProviderUnavailableError(RuntimeError):
    pass
