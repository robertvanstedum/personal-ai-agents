"""Bounded synthetic export: no provider, network, database or owner data."""
from types import SimpleNamespace
import uuid

import pytest
from core.realtime_voice.transcripts import note_parts
from minimoi_portal.guild_ui import mc_voice_export as export
from minimoi_portal.guild_ui.mc_voice_transcripts import AUTHOR_PREFIX, PAGE

SID = 'd600735e-732a-4ad0-acd3-fdd69639bd41'


def row(number, text=None, *, sid=SID, role='user', partial=False):
    _, body = note_parts(sid, role, 'private_item', 0,
                         text or f'Observed turn {number}', partial, limit=2000)[0]
    return {'id': number, 'who': AUTHOR_PREFIX + sid, 'author_kind': 'platform',
            'context': {'page': PAGE}, 'created_at': '2026-10-16T13:10:00Z',
            'text': body, 'record_mode': 'on_record'}


class Floor:
    def __init__(self, rows):
        self.rows, self.calls = rows, []
    def list_notes(self, *, before, limit):
        self.calls.append((before, limit))
        available = sorted((r for r in self.rows if before is None or r['id'] < before), key=lambda r: r['id'])
        return SimpleNamespace(ok=True, data={'notes': available[-limit:], 'more': len(available) > limit})


def test_paginated_chronology_and_humane_metadata():
    rows = [row(n) for n in range(1, 406)]
    rows[-1] = row(405, sid=str(uuid.uuid4()), role='assistant', partial=True)
    floor = Floor(rows)
    result = export.render(floor)
    assert floor.calls == [(None, 200), (206, 200), (6, 200)]
    assert result['chunks'] == 405 and result['pages'] == 3
    assert not result['truncated'] and result['next_before'] is None
    text = result['text']
    assert text.index('Observed turn 1\n') < text.index('Observed turn 200\n') < text.index('Observed turn 405\n')
    assert 'Voice session 2' in text and 'partial=true' in text
    assert SID not in text and 'private_item' not in text and AUTHOR_PREFIX not in text
    assert 'ASR' in text and 'playback is unverified' in text and 'not complete native history' in text
    assert result['complete_native_history'] is False


def test_platform_scope_excludes_owner_forgery_system_and_unrelated_notes():
    good = row(1)
    forged = dict(row(2, 'Do not export'), author_kind='owner')
    native = dict(row(3, 'Host diagnostics'), who='native-agent')
    wrong_page = dict(row(4, 'Other operation'), context={'page': 'system'})
    private = dict(row(5, 'Private body'), record_mode='off_record')
    result = export.render(Floor([good, forged, native, wrong_page, private]))
    assert result['chunks'] == 1 and result['excluded_notes'] == 4
    assert all(secret not in result['text'] for secret in ['Do not export', 'Host diagnostics', 'Other operation', 'Private body'])


def test_page_limit_cursor_resumes_without_duplicates():
    floor = Floor([row(n) for n in range(1, 406)])
    latest = export.render(floor, max_pages=1)
    older = export.render(floor, before=latest['next_before'])
    assert latest['truncated'] and latest['omission_reason'] == 'page_limit'
    assert latest['next_before'] == 206
    assert latest['chunks'] == 200 and older['chunks'] == 205
    assert 'Observed turn 206\n' in latest['text'] and 'Observed turn 206\n' not in older['text']
    assert 'Older notes remain' in latest['text']


def test_byte_limit_never_skips_unserved_row_and_unicode_is_bounded():
    floor = Floor([row(n, 'ä' * 900 + f' marker{n}') for n in range(1, 5)])
    cursor, all_text, counts = None, '', 0
    for _ in range(5):
        segment = export.render(floor, before=cursor, max_bytes=4096)
        assert len(segment['text'].encode()) <= 4096
        all_text += segment['text']
        counts += segment['chunks']
        if not segment['truncated']:
            break
        assert segment['omission_reason'] == 'byte_limit'
        cursor = segment['next_before']
    assert counts == 4 and all(all_text.count(f'marker{n}\n') == 1 for n in range(1, 5))


@pytest.mark.parametrize('change', [{'who': AUTHOR_PREFIX + 'not-a-uuid'}, {'text': 'broken'},
                                   {'created_at': 'private malformed timestamp'}, {'text': 'x' * 2001}])
def test_malformed_retained_chunk_is_counted_not_rendered(change):
    result = export.render(Floor([dict(row(1), **change)]))
    assert result['malformed_retained_chunks'] == 1 and result['chunks'] == 0
    assert 'Malformed retained chunks omitted: 1' in result['text']
    assert 'private malformed' not in result['text']


def test_read_failure_has_no_partial_success_or_private_error():
    class Broken(Floor):
        def list_notes(self, **kw):
            if self.calls: raise RuntimeError('private database body')
            return super().list_notes(**kw)
    with pytest.raises(export.ExportUnavailable, match='^Retained voice export unavailable$'):
        export.render(Broken([row(n) for n in range(1, 202)]))


@pytest.mark.parametrize('kwargs', [{'before': True}, {'before': 0}, {'max_pages': 21},
                                   {'max_bytes': 1048577}, {'max_bytes': 100}, {'max_pages': True}])
def test_closed_bounds_before_store_access(kwargs):
    with pytest.raises(export.ExportUnavailable): export.render(object(), **kwargs)


def test_invalid_store_order_or_cursor_refuses():
    for rows in [[row(2), row(1)], [row(1), row(1)], [row(5)]]:
        floor = SimpleNamespace(list_notes=lambda **kw: SimpleNamespace(ok=True, data={'notes': rows, 'more': False}))
        with pytest.raises(export.ExportUnavailable): export.render(floor, before=5)


def test_concurrent_newer_rows_not_injected_into_older_page():
    class Changing(Floor):
        def list_notes(self, **kw):
            result = super().list_notes(**kw)
            if len(self.calls) == 1: self.rows.append(row(999, 'Not in initial segment'))
            return result
    result = export.render(Changing([row(n) for n in range(1, 202)]))
    assert result['chunks'] == 201 and 'Not in initial segment' not in result['text']


def test_empty_range_and_scrub():
    assert export.render(Floor([]))['chunks'] == 0
    result = export.render(Floor([row(1, 'sensitive')]), scrub=lambda text: text.replace('sensitive', '[redacted]'))
    assert 'sensitive' not in result['text'] and '[redacted]' in result['text']


def test_twenty_page_hard_cap_and_older_cursor():
    result = export.render(Floor([row(n) for n in range(1, 4002)]))
    assert result['pages'] == 20 and result['scanned_notes'] == 4000
    assert result['truncated'] and result['next_before'] == 2
    assert len(result['text'].encode()) <= export.MAX_BYTES


def test_single_unicode_chunk_cannot_emit_stalled_cursor():
    with pytest.raises(export.ExportUnavailable, match='Single retained chunk'):
        export.render(Floor([row(1, '😀' * 1800)]), max_bytes=4096)
