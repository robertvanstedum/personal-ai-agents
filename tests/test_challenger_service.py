import sys
import types

from domains.guild.services import challenger


def test_challenger_uses_shared_xai_secret(monkeypatch):
    calls = {}

    def fake_get_secret(*args):
        calls["secret_args"] = args
        return "xai-test-key"

    class FakeCompletions:
        def create(self, **kwargs):
            calls["request"] = kwargs
            message = types.SimpleNamespace(content="[]")
            choice = types.SimpleNamespace(message=message)
            return types.SimpleNamespace(choices=[choice])

    class FakeOpenAI:
        def __init__(self, *, api_key, base_url):
            calls["api_key"] = api_key
            calls["base_url"] = base_url
            self.chat = types.SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(challenger, "get_secret", fake_get_secret)
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))

    service = challenger.ChallengerService()
    result = service._call_xai("grok-test", "Challenge this")

    assert result == "[]"
    assert calls["secret_args"] == ("XAI_API_KEY", "xai", "api_key")
    assert calls["api_key"] == "xai-test-key"
    assert calls["base_url"] == "https://api.x.ai/v1"


def test_challenger_uses_shared_anthropic_secret(monkeypatch):
    calls = {}

    def fake_get_secret(*args):
        calls["secret_args"] = args
        return "anthropic-test-key"

    class FakeMessages:
        def create(self, **kwargs):
            calls["request"] = kwargs
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(text='{"final":"reviewed"}')]
            )

    class FakeAnthropic:
        def __init__(self, *, api_key):
            calls["api_key"] = api_key
            self.messages = FakeMessages()

    monkeypatch.setattr(challenger, "get_secret", fake_get_secret)
    monkeypatch.setitem(
        sys.modules, "anthropic", types.SimpleNamespace(Anthropic=FakeAnthropic)
    )

    service = challenger.ChallengerService()
    result = service._call_anthropic("claude-test", "Review this", max_tokens=200)

    assert result == '{"final":"reviewed"}'
    assert calls["secret_args"] == (
        "ANTHROPIC_API_KEY",
        "anthropic",
        "api_key",
    )
    assert calls["api_key"] == "anthropic-test-key"
