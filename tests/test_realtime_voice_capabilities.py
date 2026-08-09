import pytest

from core.realtime_voice.capabilities import (
    MEMO_MODE,
    ProviderUnavailableError,
    get_provider_capability,
    providers_for_mode,
    resolve_memo_provider,
)


def test_openai_memo_is_browser_direct_and_available():
    capability = get_provider_capability("openai")
    assert capability.streaming_transcription is True
    assert capability.browser_transport == "webrtc"
    assert capability.browser_direct_ephemeral_auth is True
    assert capability.server_proxy_required is False
    assert capability.memo_available is True


def test_xai_memo_declares_real_stt_but_requires_unbuilt_server_proxy():
    capability = get_provider_capability("xai")
    assert capability.streaming_transcription is True
    assert capability.browser_transport == "websocket"
    assert capability.browser_direct_ephemeral_auth is False
    assert capability.server_proxy_required is True
    assert capability.memo_available is False
    assert "server-side WebSocket proxy" in capability.unavailable_reason


def test_only_available_memo_providers_are_returned_to_ui():
    providers = providers_for_mode(MEMO_MODE, "de-AT")
    assert [provider.provider for provider in providers] == ["openai"]


def test_xai_memo_request_fails_closed_until_proxy_exists():
    with pytest.raises(ProviderUnavailableError, match="WebSocket proxy"):
        resolve_memo_provider("xai", "pt-BR")


def test_invalid_provider_is_rejected():
    with pytest.raises(ValueError, match="Unknown voice provider"):
        resolve_memo_provider("browser-speech-recognition", "de-AT")
