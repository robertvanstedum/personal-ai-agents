"""Bounded metadata/text from provider-final events (server-held, or posted by the owner's page on the direct path,
validated here); no IO."""
import re
import uuid
from datetime import datetime, timezone

MAX_BYTES = 16000
MAX_CONTENT = 32


def assistant_entries(response, *, interrupted=False):
    output = response.get('output', [])
    if not isinstance(output, list) or len(output) > MAX_CONTENT:
        raise ValueError('voice_transcript_shape')
    partial = response.get('status') != 'completed' or interrupted
    entries, total = [], 0
    for item in output:
        if not isinstance(item, dict):
            raise ValueError('voice_transcript_shape')
        if item.get('type') != 'message' or item.get('role') != 'assistant':
            continue
        content = item.get('content', [])
        if not isinstance(content, list) or len(content) > MAX_CONTENT:
            raise ValueError('voice_transcript_shape')
        for index, part in enumerate(content):
            if not isinstance(part, dict):
                raise ValueError('voice_transcript_shape')
            if part.get('type') in {'audio', 'output_audio'}:
                text = part.get('transcript')
            elif part.get('type') in {'text', 'output_text'}:
                text = part.get('text')
            else:
                continue
            if text in (None, ''):
                continue
            if not isinstance(text, str):
                raise ValueError('voice_transcript_shape')
            total += len(text.encode())
            if total > MAX_BYTES:
                raise ValueError('voice_transcript_bound')
            entries.append((item.get('id'), index, text, partial))
    return entries


# Speakers a retained note names. user/assistant are the provider's turns; consult/receipt are the direct path's
# bridged consult and its outcome (Spec 164 §6). Callers that pass neither stamps nor interrupted get the old header.
SPEAKERS = {'user': 'owner ASR', 'assistant': 'assistant generated; playback unverified',
            'consult': 'consult request', 'receipt': 'consult receipt'}
STAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z')


def stamp_time(text):
    """The UTC datetime of a STAMP ("…T20:00:00Z", "…T20:00:00.5Z"), or None. Stamps are compared as times, never as
    strings: "20:00:00Z" sorts after "20:00:00.5Z" as text (review B #6)."""
    if not isinstance(text, str) or not STAMP.fullmatch(text):
        return None
    try:
        return datetime.strptime(text, '%Y-%m-%dT%H:%M:%S.%fZ' if '.' in text else '%Y-%m-%dT%H:%M:%SZ'
                                 ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def note_parts(sid, role, item, index, text, partial, *, limit, started=None, ended=None, interrupted=False):
    if (role not in SPEAKERS or type(index) is not int or not 0 <= index < MAX_CONTENT
            or not isinstance(text, str) or not 0 < len(text.encode()) <= MAX_BYTES
            or type(partial) is not bool or type(limit) is not int or not 512 <= limit <= 4000
            or type(interrupted) is not bool or (interrupted and not partial)):
        raise ValueError('voice_transcript_bound')
    session = uuid.UUID(sid)
    if not isinstance(item, str) or not 1 <= len(item) <= 160 or not all(c.isascii() and (c.isalnum() or c in '_-') for c in item):
        raise ValueError('voice_transcript_identity')
    tail = ']\n'
    if started is not None or ended is not None:
        # Start and end of the turn (UTC, ISO 8601 Z): overlap and latency stay representable later.
        times = [stamp_time(t) for t in (started, ended)]
        if None in times or times[0] > times[1]:
            raise ValueError('voice_transcript_time')
        tail = f'; start={started}; end={ended}' + tail
    if interrupted:
        tail = tail[:-2] + '; interrupted=true]\n'
    who = SPEAKERS[role]
    # Fixed-width upper-bound header reserves space for every part count.
    prefix = f'[Voice transcript: {who}; partial={str(partial).lower()}; '
    available = limit - len(prefix) - len('part=999/999') - len(tail)
    if available < 1:
        raise ValueError('voice_transcript_bound')
    pieces = [text[start:start + available] for start in range(0, len(text), available)]
    return [(str(uuid.uuid5(session, f'transcript:{role}:{item}:{index}:{part}')),
             prefix + f'part={part + 1}/{len(pieces)}' + tail + piece)
            for part, piece in enumerate(pieces)]
