"""Deterministic export of retained voice notes on an already-authorized floor.

The HTTP caller MUST freshly authorize the owned, on-record, unarchived
conversation and its floor before AND after this read. No access is inferred here.
"""
from datetime import datetime
import uuid

from .mc_voice_transcripts import AUTHOR_PREFIX, HEADER, PAGE
from .stores import NOTE_MAX

MAX_BYTES = 1024 * 1024
MAX_PAGES = 20
INTRO = (
    'Saved voice conversation — retained observed turns only\n'
    'Owner speech is automatic transcription (ASR), not verified verbatim. '
    'Assistant text was generated; playback is unverified. Partial=true marks '
    'an interrupted or incomplete response. Missing chunks, unretained turns and '
    'events arriving after End may be absent. Session labels are local to this export segment. '
    'This is not complete native history.\n'
)
# Reserve room for a fixed, bounded ending even when the body reaches its cap.
ENDING_RESERVE = 512


class ExportUnavailable(ValueError):
    """Fixed safe failure; never include stored text or private diagnostics."""


def _entry(row):
    """Return trusted platform text only; never export machine correlation IDs."""
    who, context = row.get('who'), row.get('context')
    if (row.get('author_kind') != 'platform' or not isinstance(who, str)
            or not who.startswith(AUTHOR_PREFIX) or not isinstance(context, dict)
            or context.get('page') != PAGE
            or row.get('record_mode', 'on_record') != 'on_record'):
        return None
    try:
        raw_session = who[len(AUTHOR_PREFIX):]
        session = str(uuid.UUID(raw_session))
        if raw_session != session:
            raise ValueError()
        text = row['text']
        if not isinstance(text, str) or len(text) > NOTE_MAX:
            raise ValueError()
        match = HEADER.match(text)
        if match is None:
            raise ValueError()
        part, total = int(match['part']), int(match['total'])
        if not 1 <= part <= total <= 999:
            raise ValueError()
        stamp = datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ExportUnavailable('Retained voice note malformed') from None
    return session, match['speaker'], stamp.isoformat(), part, total, match['partial'], text[match.end():]


def render(floor, *, before=None, max_pages=MAX_PAGES, max_bytes=MAX_BYTES, scrub=lambda text: text):
    """Read <=20 pages/4000 notes, emit <=1MiB UTF8, with an older-page cursor.

    Each returned segment is in stable ascending note-ID chronology. Reads use
    exclusive `before` cursors, so concurrent newer writes cannot be duplicated
    into later pages. This is not a cross-page database snapshot. The cursor is
    scoped by the caller's floor and must never choose another conversation.
    """
    if (before is not None and (type(before) is not int or before < 1)
            or type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES
            or type(max_bytes) is not int or not 4096 <= max_bytes <= MAX_BYTES):
        raise ExportUnavailable('Export bounds invalid')
    entries, pages, scanned, ignored, invalid = [], 0, 0, 0, 0
    cursor, last_scanned, reason, more = before, None, None, False
    body_bytes = len(INTRO.encode()) + ENDING_RESERVE
    for _ in range(max_pages):
        try:
            result = floor.list_notes(before=cursor, limit=200)
            data = result.data
            rows, more = data['notes'], data['more']
            if not result.ok or not isinstance(rows, list) or len(rows) > 200 or type(more) is not bool:
                raise ValueError()
            ids = [row['id'] for row in rows]
            if (any(type(value) is not int or value < 1 for value in ids)
                    or ids != sorted(set(ids)) or (cursor is not None and any(value >= cursor for value in ids))
                    or (more and not rows)):
                raise ValueError()
        except Exception:
            raise ExportUnavailable('Retained voice export unavailable') from None
        pages += 1
        for row in reversed(rows):
            try:
                parsed = _entry(row)
            except ExportUnavailable:
                invalid += 1
                parsed = None
            if parsed is not None:
                session, speaker, stamp, part, total, partial, text = parsed
                try:
                    text = scrub(text)
                except Exception:
                    raise ExportUnavailable('Export scrub unavailable') from None
                if not isinstance(text, str) or len(text) > NOTE_MAX:
                    raise ExportUnavailable('Export scrub unavailable')
                # The reserved session label is longer than any possible ordinal.
                size = len((f'Voice session 9999 · {speaker} · {stamp} · part {part}/{total} · '
                            f'partial={partial} · retained provider transcript\n{text}\n\n').encode())
                if body_bytes + size > max_bytes:
                    if not entries:
                        raise ExportUnavailable('Single retained chunk exceeds export bound')
                    reason = 'byte_limit'
                    break
                entries.append((row['id'], session, speaker, stamp, part, total, partial, text))
                body_bytes += size
            else:
                ignored += 1
            scanned += 1
            last_scanned = row['id']
        if reason is not None or not more:
            break
        cursor = rows[0]['id']
    else:
        if more:
            reason = 'page_limit'
    truncated = reason is not None
    # Never emit a misleading resume cursor that skips an unserved voice row.
    next_before = last_scanned if truncated else None
    sessions, chunks = {}, []
    for _, session, speaker, stamp, part, total, partial, text in sorted(entries):
        sessions.setdefault(session, len(sessions) + 1)
        chunks.append(f'Voice session {sessions[session]} · {speaker} · {stamp} · part {part}/{total} · '
                      f'partial={partial} · retained provider transcript\n{text}\n\n')
    end = (f'Exported {len(entries)} retained chunks from {pages} page(s); '
           f'{scanned} floor notes examined. Non-voice notes excluded. '
           f'Malformed retained chunks omitted: {invalid}.\n')
    end += ('Older notes remain unserved because of the export limit; request the next older segment.\n'
            if truncated else 'All floor pages in this requested range were scanned; unretained speech may still be missing.\n')
    if not entries:
        end += 'No exportable retained voice turn found in this requested range.\n'
    text = INTRO + '\n' + ''.join(chunks) + end
    if len(text.encode()) > max_bytes:
        raise ExportUnavailable('Export bound exceeded')
    return {'text': text, 'chunks': len(entries), 'sessions': len(sessions),
            'pages': pages, 'scanned_notes': scanned, 'excluded_notes': ignored - invalid,
            'malformed_retained_chunks': invalid, 'truncated': truncated,
            'omission_reason': reason, 'next_before': next_before,
            'complete_native_history': False}
