import pytest

from core.realtime_voice.capabilities import (
    AGENT_CONVERSATION_MODE,
    MEMO_MODE,
    ProviderUnavailableError,
    get_provider_capability,
    providers_for_mode,
    resolve_memo_provider,
    resolve_agent_conversation_provider,
)


def test_openai_memo_is_browser_direct_and_available():
    capability = get_provider_capability("openai")
    assert capability.streaming_transcription is True
    assert capability.streaming_speech is True
    assert capability.browser_transport == "webrtc"
    assert capability.browser_direct_ephemeral_auth is True
    assert capability.server_proxy_required is False
    assert capability.memo_available is True
    assert capability.agent_conversation_available is True


def test_xai_browser_voice_is_direct_but_memo_mode_remains_unavailable():
    capability = get_provider_capability("xai")
    assert capability.streaming_transcription is True
    assert capability.browser_transport == "websocket"
    assert capability.browser_direct_ephemeral_auth is True
    assert capability.server_proxy_required is False
    assert capability.memo_available is False
    assert capability.agent_conversation_available is True
    assert capability.unavailable_reason is None


def test_only_available_memo_providers_are_returned_to_ui():
    providers = providers_for_mode(MEMO_MODE, "de-AT")
    assert [provider.provider for provider in providers] == ["openai"]


def test_realtime_confer_provider_choices_are_available():
    providers = providers_for_mode(AGENT_CONVERSATION_MODE, "en-US")
    assert [provider.provider for provider in providers] == ["openai", "xai"]
    assert resolve_agent_conversation_provider("openai", "en-US").provider == "openai"
    assert resolve_agent_conversation_provider("xai", "en-US").provider == "xai"


def test_xai_memo_request_fails_closed_for_unsupported_mode():
    with pytest.raises(ProviderUnavailableError, match="unavailable for memo"):
        resolve_memo_provider("xai", "pt-BR")


def test_invalid_provider_is_rejected():
    with pytest.raises(ValueError, match="Unknown voice provider"):
        resolve_memo_provider("browser-speech-recognition", "de-AT")
