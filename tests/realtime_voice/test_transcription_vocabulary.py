"""A4-2 (A4_FIX_BRIEF.md item 3): MiniMoi's command words for the transcriber.

Robert said "save a note test" and it was transcribed as German ("selber Not test") under D19's bilingual transcription.
A direct-path host (MC, CoS) now names MiniMoi's own names and command words to the transcriber where the provider takes a
hint: on OpenAI one sentence added to the transcription prompt (its only hint field; the session.updated it echoes has
"prompt"). xAI's session has no such field (its echoed session.updated shows only the transcription model and
language_hint), so nothing is sent there and its mint is unchanged. The hint names words, never a language, so the session
stays bilingual. German and Portuguese never send it (test_direct_host_a2's exact-arguments test still holds). Session
config only: the provider mint is a stub or a patched HTTP post. No provider call, no Keychain, no network."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.realtime_voice.providers import openai_realtime, xai_voice
from core.realtime_voice.standards import BILINGUAL_TRANSCRIPTION_HINT, TRANSCRIPTION_VOCABULARY, vocabulary_hint

from test_direct_host_a2 import boot, build


def test_the_vocabulary_names_minimois_names_and_command_words():
    assert TRANSCRIPTION_VOCABULARY == ("MiniMoi", "Master Craftsman", "Chief of Staff", "Claude Code", "Codex", "Grok",
                                        "make a note that", "save a note", "post-it", "board", "Check with MC",
                                        "Check with CoS")
    hint = vocabulary_hint(TRANSCRIPTION_VOCABULARY)
    assert all(term in hint for term in TRANSCRIPTION_VOCABULARY) and len(hint) < 300
    for forcing in ("English", "German", "language", "translate", "only"):          # it never asks for a language
        assert forcing.casefold() not in hint.casefold(), forcing


@pytest.mark.parametrize("provider", ["openai", "xai"])
def test_a_bilingual_direct_host_sends_the_vocabulary_only_where_the_provider_takes_it(monkeypatch, tmp_path, provider):
    r = build(monkeypatch, tmp_path, card={"card": "Test Person.", "transcription": "bilingual",
                                           "voices": {"openai": "cedar", "xai": "ara"}})
    assert boot(r, provider).status_code == 200
    (minted,) = r.mints
    assert minted["transcription_language"] is None                                  # still bilingual
    if provider == "openai":
        assert minted["transcription_vocabulary"] == TRANSCRIPTION_VOCABULARY
        assert minted["transcription_prompt"] == BILINGUAL_TRANSCRIPTION_HINT          # the faithful hint, unchanged
    else:
        assert "transcription_vocabulary" not in minted and "transcription_prompt" not in minted


def test_a_direct_host_with_one_language_still_names_its_words_on_openai(monkeypatch, tmp_path):
    r = build(monkeypatch, tmp_path)
    assert boot(r).status_code == 200
    (minted,) = r.mints
    assert minted["transcription_language"] == "en" and "transcription_prompt" not in minted
    assert minted["transcription_vocabulary"] == TRANSCRIPTION_VOCABULARY


def _response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def _openai_transcription(**kw):
    with (
        patch("core.realtime_voice.providers.openai_realtime.get_secret", return_value="secret"),
        patch("core.realtime_voice.providers.openai_realtime.requests.post",
              return_value=_response({"value": "ephemeral", "expires_at": 123})) as post,
    ):
        openai_realtime.mint_ephemeral_credential(instructions="i", voice="marin", turn_detection={"type": "server_vad"},
                                                  user_id_for_safety_identifier="3", **kw)
    return post.call_args.kwargs["json"]["session"]["audio"]["input"]["transcription"]


def test_openai_carries_the_vocabulary_in_its_transcription_prompt_after_the_faithful_hint():
    got = _openai_transcription(transcription_language=None, transcription_prompt=BILINGUAL_TRANSCRIPTION_HINT,
                                transcription_vocabulary=TRANSCRIPTION_VOCABULARY)
    assert got == {"model": "gpt-4o-transcribe",
                   "prompt": BILINGUAL_TRANSCRIPTION_HINT + " " + vocabulary_hint(TRANSCRIPTION_VOCABULARY)}
    alone = _openai_transcription(transcription_language="en", transcription_vocabulary=TRANSCRIPTION_VOCABULARY)
    assert alone == {"model": "gpt-4o-transcribe", "language": "en", "prompt": vocabulary_hint(TRANSCRIPTION_VOCABULARY)}


@pytest.mark.parametrize("language,prompt", [("de", None), ("pt", None), (None, BILINGUAL_TRANSCRIPTION_HINT)])
def test_openai_without_a_vocabulary_is_exactly_as_before(language, prompt):
    got = _openai_transcription(transcription_language=language, **({"transcription_prompt": prompt} if prompt else {}))
    assert got == {"model": "gpt-4o-transcribe", **({"language": language} if language else {}),
                   **({"prompt": prompt} if prompt else {})}


def test_xai_has_no_vocabulary_field_and_its_session_is_unchanged():
    import inspect
    assert "transcription_vocabulary" not in inspect.signature(xai_voice.mint_ephemeral_credential).parameters
    with (
        patch("core.realtime_voice.providers.xai_voice.get_secret", return_value="secret"),
        patch("core.realtime_voice.providers.xai_voice.requests.post", return_value=_response({"token": "t"})),
    ):
        got = xai_voice.mint_ephemeral_credential(instructions="i", voice="leo", turn_detection={"type": "server_vad"},
                                                  transcription_language=None)
    assert got["session_config"]["audio"]["input"]["transcription"] == {"model": "grok-transcribe"}

