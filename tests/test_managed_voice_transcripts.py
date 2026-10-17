"""Synthetic trusted-provider retention; no network, provider or real storage."""
from types import SimpleNamespace
import uuid

import pytest
from flask import Flask

from core.realtime_voice import controlled_session as controlled
from core.realtime_voice.transcripts import assistant_entries, note_parts
from minimoi_portal.guild_ui import mc_voice_transcripts as notes

SID = 'd600735e-732a-4ad0-acd3-fdd69639bd41'
USAGE = {'input_tokens': 2, 'output_tokens': 2,
         'input_token_details': {'text_tokens': 1, 'audio_tokens': 1, 'image_tokens': 0},
         'output_token_details': {'text_tokens': 1, 'audio_tokens': 1}}
ASR = {'type': 'tokens', 'input_tokens': 1, 'output_tokens': 1,
       'input_token_details': {'text_tokens': 0, 'audio_tokens': 1}}


class Journal:
    def create(self, *args): return SID
    def provider_identity(self, *args): pass
    def state(self, *args): pass
    def usage(self, *args): return 1, 1000000
    def record_end(self, sid, receipt, call): return receipt


class Provider:
    def create(self, *args): return 'synthetic answer', 'rtc_synthetic'
    def attach(self, *args): pass
    def send(self, *args): pass
    def hangup(self, *args): pass


@pytest.fixture
def make_session():
    sessions = []
    def make(callback=None):
        session = controlled.ControlledSession(
            journal=Journal(), budget=SimpleNamespace(reserve=lambda **kw: 'reservation'),
            provider=Provider(), principal='owner', conversation='c-synthetic',
            allowance_micro=1000000, dispatch=lambda *args: pytest.fail('unexpected native effect'),
            on_transcript=callback)
        session.start('synthetic SDP', {'model': controlled.MODEL, 'type': 'realtime',
                       'audio': {'input': {'turn_detection': {'create_response': False}}}})
        sessions.append(session)
        return session
    yield make
    for session in sessions:
        session.end('user_ended')
        session.worker.shutdown(wait=True)


def transcription(session, text='Hello', item='input_private'):
    session.observe({'type': 'conversation.item.input_audio_transcription.completed',
                     'item_id': item, 'transcript': text, 'usage': ASR})


def response(session, *, status='completed', text='Servus', interrupted=False):
    session.observe({'type': 'input_audio_buffer.committed', 'item_id': 'input_private'})
    session.observe({'type': 'response.created', 'response': {'id': 'response_private'}})
    if interrupted:
        session.observe({'type': 'input_audio_buffer.speech_started'})
    session.observe({'type': 'response.done', 'response': {
        'id': 'response_private', 'status': status, 'usage': USAGE,
        'output': [{'id': 'assistant_private', 'type': 'message', 'role': 'assistant',
                    'content': [{'type': 'output_audio', 'transcript': text}]}]}})


def test_trusted_asr_duplicates_and_conflicts(make_session):
    seen = []
    session = make_session(lambda *args: seen.append(args) or {'saved': True})
    transcription(session)
    transcription(session)
    assert len(seen) == 1 and seen[0] == (SID, 'c-synthetic', 'user', 'input_private', 0, 'Hello', False)
    transcription(session, 'different')
    assert session.closed and session.end_receipt['transcript_retention'] == 'failed'


@pytest.mark.parametrize('status,interrupted,partial', [('completed', False, False), ('cancelled', False, True), ('completed', True, True)])
def test_assistant_final_and_barged_text_is_labelled(make_session, status, interrupted, partial):
    seen = []
    session = make_session(lambda *args: seen.append(args) or {'saved': True})
    response(session, status=status, interrupted=interrupted)
    assert seen[0][2:] == ('assistant', 'assistant_private', 0, 'Servus', partial)
    assert not session.closed
    receipt = session.end('user_ended')
    assert receipt['transcript_entries'] == 1 and receipt['transcript_may_be_incomplete']
    transcription(session, 'late')
    assert len(seen) == 1


def test_absent_callback_preserves_legacy_host(make_session):
    session = make_session()
    response(session, text='x' * 16001)
    assert not session.closed
    assert 'transcript_retention' not in session.end('user_ended')


@pytest.mark.parametrize('reply', [None, {'saved': False}, RuntimeError('private body')])
def test_retention_failure_is_explicit_and_private(make_session, reply):
    def callback(*args):
        if isinstance(reply, Exception): raise reply
        return reply
    session = make_session(callback)
    transcription(session)
    assert session.closed and session.end_receipt['transcript_retention'] == 'failed'
    assert 'private body' not in str(session.end_receipt)


def test_response_byte_bound_checked_before_any_entry():
    output = [{'type': 'message', 'role': 'assistant', 'id': 'one',
               'content': [{'type': 'text', 'text': 'a' * 8000}, {'type': 'audio', 'transcript': 'ä' * 4001}]}]
    with pytest.raises(ValueError): assistant_entries({'output': output})
    assert assistant_entries({'output': [{'type': 'function_call', 'arguments': 'secret'}]}) == []


def test_chunk_keys_bounds_unicode_and_private_item_identity():
    parts = note_parts(SID, 'user', 'provider_private', 0, 'ä' * 8000, False, limit=2000)
    assert len(parts) > 1 and all(len(text) <= 2000 for _, text in parts)
    assert parts == note_parts(SID, 'user', 'provider_private', 0, 'ä' * 8000, False, limit=2000)
    assert len({key for key, _ in parts}) == len(parts)
    assert all(uuid.UUID(key) and 'provider_private' not in text and SID not in text for key, text in parts)
    assert parts != note_parts(SID, 'assistant', 'provider_private', 0, 'ä' * 8000, False, limit=2000)


class Floor:
    def __init__(self): self.rows = {}; self.calls = 0
    def add_note(self, request_id, text, author, **context):
        self.calls += 1
        old = self.rows.get(request_id)
        if old and old['text'] != text:
            return SimpleNamespace(outcome='idempotency_mismatch', value=None)
        self.rows[request_id] = dict(text=text, who=author.id, author_kind=author.kind,
                                    context=context, created_at='2026-10-16T12:00:00Z')
        return SimpleNamespace(outcome='kept', value=self.rows[request_id])
    def list_notes(self, *, limit):
        assert limit == 200
        return SimpleNamespace(ok=True, data={'notes': list(self.rows.values())[-limit:], 'more': False})


def binding(monkeypatch):
    from minimoi_portal.guild_ui import api
    monkeypatch.setattr(api, '_clean_text', lambda text, *args: (text.replace('sensitive', '[redacted]'), None))
    fresh = {'principal': 'owner', 'notes_floor': 'private-floor', 'record_mode': 'on_record', 'archived': False}
    floor = Floor()
    bound = {'app': Flask(__name__), 'config': {}, 'principal': 'owner', 'conversation': dict(fresh),
             'conversations': SimpleNamespace(get=lambda cid, who: dict(fresh)), 'floor': floor}
    return bound, fresh, floor


def test_scoped_retention_redaction_and_existing_idempotency(monkeypatch):
    bound, fresh, floor = binding(monkeypatch)
    for _ in range(2): notes.retain(bound, 'c-synthetic', SID, 'user', 'item', 0, 'sensitive', False)
    assert len(floor.rows) == 1 and '[redacted]' in next(iter(floor.rows.values()))['text']
    row = next(iter(floor.rows.values()))
    assert SID not in row['text'] and row['who'] == notes.AUTHOR_PREFIX + SID
    with pytest.raises(controlled.SessionRefused):
        notes.retain(bound, 'c-synthetic', SID, 'user', 'item', 0, 'changed', False)


@pytest.mark.parametrize('change', [{'principal': 'other'}, {'archived': True}, {'record_mode': 'off_record'}, {'notes_floor': 'other'}])
def test_fresh_scope_guard_before_every_chunk(monkeypatch, change):
    bound, fresh, floor = binding(monkeypatch)
    original = floor.add_note
    def add(*args, **kwargs):
        result = original(*args, **kwargs)
        fresh.update(change)
        return result
    floor.add_note = add
    with pytest.raises(controlled.SessionRefused):
        notes.retain(bound, 'c-synthetic', SID, 'user', 'item', 0, 'x' * 5000, False)
    assert len(floor.rows) == 1


def test_context_only_retained_platform_turns_humane_metadata(monkeypatch):
    bound, fresh, floor = binding(monkeypatch)
    notes.retain(bound, 'c-synthetic', SID, 'assistant', 'provider_private', 0, 'A retained sentence.', False)
    floor.rows['forged'] = dict(text='host diagnostic PRIVATE', who=notes.AUTHOR_PREFIX + SID,
                                author_kind='owner', context={'page': notes.PAGE})
    result = notes.block('Print the voice transcript', floor)
    assert 'A retained sentence.' in result and 'Voice session 1' in result
    assert SID not in result and 'provider_private' not in result and 'host diagnostic PRIVATE' not in result
    assert 'observed excerpt' in result and 'playback unverified' in result and 'omit' in result
    assert len(result) <= 12000
    assert notes.block('hello', SimpleNamespace()) == ''


def test_context_whole_output_bound_and_unavailable(monkeypatch):
    bound, fresh, floor = binding(monkeypatch)
    for index in range(20):
        notes.retain(bound, 'c-synthetic', str(uuid.uuid4()), 'user', 'item', 0, 'a' * 1900, False)
    result = notes.block('transcript', floor)
    assert len(result) <= 12000 and result.count('retained provider transcript') < 20
    unavailable = SimpleNamespace(list_notes=lambda **kw: SimpleNamespace(ok=False))
    assert 'unavailable' in notes.block('transcript', unavailable)


def test_late_duplicate_done_does_not_reclassify_first_retained_partial(make_session):
    seen = []
    session = make_session(lambda *args: seen.append(args) or {'saved': True})
    response(session)
    event = {'type': 'response.done', 'response': {
        'id': 'response_private', 'status': 'completed', 'usage': USAGE,
        'output': [{'id': 'assistant_private', 'type': 'message', 'role': 'assistant',
                    'content': [{'type': 'output_audio', 'transcript': 'Servus'}]}]}}
    session.observe({'type': 'input_audio_buffer.speech_started'})
    session.observe(event)
    assert not session.closed and len(seen) == 1 and seen[0][-1] is False
    event['response']['output'][0]['content'][0]['transcript'] = 'changed'
    session.observe(event)
    assert session.closed and session.end_receipt['transcript_retention'] == 'failed'
