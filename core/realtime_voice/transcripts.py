"""Bounded metadata/text from trusted provider-final events; no IO or browser input."""
import uuid

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


def note_parts(sid, role, item, index, text, partial, *, limit):
    if (role not in {'user', 'assistant'} or type(index) is not int or not 0 <= index < MAX_CONTENT
            or not isinstance(text, str) or not 0 < len(text.encode()) <= MAX_BYTES
            or type(partial) is not bool or type(limit) is not int or not 512 <= limit <= 4000):
        raise ValueError('voice_transcript_bound')
    session = uuid.UUID(sid)
    if not isinstance(item, str) or not 1 <= len(item) <= 160 or not all(c.isascii() and (c.isalnum() or c in '_-') for c in item):
        raise ValueError('voice_transcript_identity')
    who = 'owner ASR' if role == 'user' else 'assistant generated; playback unverified'
    # Fixed-width upper-bound header reserves space for every part count.
    prefix = f'[Voice transcript: {who}; partial={str(partial).lower()}; '
    available = limit - len(prefix) - len('part=999/999]\n')
    if available < 1:
        raise ValueError('voice_transcript_bound')
    pieces = [text[start:start + available] for start in range(0, len(text), available)]
    return [(str(uuid.uuid5(session, f'transcript:{role}:{item}:{index}:{part}')),
             prefix + f'part={part + 1}/{len(pieces)}]\n' + piece)
            for part, piece in enumerate(pieces)]
