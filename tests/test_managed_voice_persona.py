"""In-memory config/host tests only; no DB, provider, microphone or app startup."""
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from core.realtime_voice import controlled_config as config
from core.realtime_voice import controlled_host
from core.realtime_voice.controlled_session import SessionRefused

# Captured from c9eb's actual configuration, removing ONLY instructions and the
# old transcription.language. SOURCE.json binds that original source hash.
BASE_REMAINDER_SHA256 = '180018bfaace4e60767938c852fed6dad69ab45691332c4e189a1f5dd5350ece'


def test_shared_persona_keeps_host_authority_and_truthful_boundaries():
    host = 'Host authority: owner-authenticated operations only; synthetic context.'
    result = config.configuration(host)
    assert result['instructions'] == host + '\n\n' + config.VOICE_MANNER
    assert all(word in config.VOICE_MANNER for word in ('Austrian standard German', 'natural English',
               'current language', 'consult_cos_agent', 'complete native conversation history',
               'Guild conversation note', 'Board operation receipt', 'do not expand authority'))


def test_only_instructions_and_transcription_hint_change_from_frozen_base():
    result = copy.deepcopy(config.configuration('host-marker'))
    result.pop('instructions')
    transcription = result['audio']['input']['transcription']
    assert transcription.pop('prompt') == config.TRANSCRIPTION_HINT
    assert 'language' not in transcription
    digest = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert digest == BASE_REMAINDER_SHA256


@pytest.mark.parametrize('domain', ['mc', 'cos'])
def test_both_managed_hosts_receive_same_persona_without_provider_or_db(monkeypatch, domain):
    captured = {}
    monkeypatch.setattr(controlled_host, 'configuration', config.configuration)
    monkeypatch.setattr(controlled_host, 'blueprint', lambda **kwargs: captured.update(kwargs) or 'synthetic-blueprint')
    def no_io(*args, **kwargs): pytest.fail('config construction touched provider/DB/guard')
    result = controlled_host.create_host_blueprint(name='synthetic', domain=domain, guard=no_io,
        conversation=no_io, instructions=lambda owner: 'Exact ' + domain + ' host instructions',
        dispatch=no_io, provider_factory=no_io, environ={}, budget=SimpleNamespace(_tx=no_io))
    assert result == 'synthetic-blueprint'
    assert captured['build_config']('owner')['instructions'].endswith(config.VOICE_MANNER)


def test_persona_combined_utf8_limit_remains_strict():
    overhead = len(('\n\n' + config.VOICE_MANNER).encode())
    exact = 'x' * (24000 - overhead)
    assert len(config.configuration(exact)['instructions'].encode()) == 24000
    with pytest.raises(SessionRefused): config.configuration(exact + 'x')
    # Count UTF-8 bytes, not Python characters.
    with pytest.raises(SessionRefused): config.configuration('ä' * 12000)
    with pytest.raises(SessionRefused): config.configuration('')


def test_language_hint_is_optional_in_installed_sdk_contract():
    from openai.types.realtime.audio_transcription_param import AudioTranscriptionParam
    assert 'language' in AudioTranscriptionParam.__optional_keys__
    assert 'prompt' in AudioTranscriptionParam.__optional_keys__
    value = config.configuration('Host')['audio']['input']['transcription']
    assert value['model'] == 'gpt-4o-transcribe'
    assert 'English or Austrian German' in value['prompt']
