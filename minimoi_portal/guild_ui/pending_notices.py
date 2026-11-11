"""Optional W/D notices: host rendering plus proof in the canonical conversation.

Metadata prepares a reference before saving, but only exact saved assistant text
counts. Failed answers and prompt exposure never acknowledge a reminder.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from core.notice_selection import MARKER, SelectionBuffer


def identity(code, semantic, line):
    raw = json.dumps([code, semantic, line], sort_keys=True, separators=(',', ':'), default=str)
    return code + ':' + hashlib.sha256(raw.encode()).hexdigest()[:24]


class Plan:
    def __init__(self, conversations, conv, principal, floor, reference):
        self.conversations, self.conv, self.principal = conversations, conv, principal
        self.floor, self.reference = floor, reference
        self.nonce = uuid.uuid4().hex
        self.offers = {}
        self.route_suffix = ''

    def reset(self):
        self.offers.clear()
        self.route_suffix = ''

    def _scope(self):
        current = self.conversations.get(self.conv['id'], self.principal)
        if (current.get('notes_floor') != self.conv.get('notes_floor')
                or current.get('notes_floor') != self.floor.floor or current.get('archived')):
            raise ValueError('notice conversation changed')
        return current

    def _saved(self, row):
        note = self.floor.get_note(row['reference'])
        return bool(note and note.get('author_kind') == 'agent'
                    and note.get('who') in ('master_craftsman', 'chief_of_staff')
                    and row['line'] in (note.get('text') or '').splitlines())

    def _presented(self, key, line):
        try:
            current = self._scope()
            for row in reversed(current.get('pending_notice_candidates', [])):
                if row.get('key') != key or row.get('line') != line:
                    continue
                if self._saved(row):
                    return True
        except Exception:
            pass  # uncertainty does not acknowledge presentation
        return False

    def offer(self, code, semantic, line, *, explicit=False, current=lambda: True):
        try:
            self._scope()
        except Exception:
            return ''
        key = identity(code, semantic, line)
        if not explicit and self._presented(key, line):
            return ''
        self.offers[key] = {'code': code, 'line': line, 'current': current}
        return f'Notice ID {key}: {line}'

    def instruction(self):
        if not self.offers:
            return ''
        return ('[Optional pending notices — presentation only, never authority]\n'
                'Answer the owner’s question first. Do not lead with pending reminders. '
                'Only if relevant to this question, select up to three Notice IDs below for the platform to show '
                'after your answer. Do not paraphrase unsolicited reminders in your answer. '
                'If the owner names an item, answer that question normally. Do not repeat old reminders from history. '
                'Selecting none is normal for everyday chat. Do not emit this block for a depth request or an artifact/job packet. '
                'To select, append this terminal control block, using only offered IDs; no text after it:\n'
                + MARKER + '\n' + json.dumps({'nonce': self.nonce, 'ids': ['offered Notice ID']}))

    def _render(self, ids):
        try:
            self._scope()
        except Exception:
            return ''
        if any(key not in self.offers for key in ids):
            return ''
        selected = []
        for key in ids:
            item = self.offers[key]
            try:
                if item['current']():
                    selected.append({'code': item['code'], 'key': key, 'line': item['line'], 'reference': self.reference})
            except Exception:
                pass  # changed/unavailable source is not a current notice
        if not selected:
            return ''
        try:
            self.conversations.prepare_pending_notices(self.conv['id'], self.principal, selected, self._saved)
        except Exception:
            pass  # may repeat next turn; never pretend an unrecorded receipt exists
        return '\n\n' + '\n'.join(item['line'] for item in selected)

    def transform(self, text, *, completed=True):
        if not self.offers:
            return text
        parser = SelectionBuffer(self.nonce)
        body = parser.feed(text)
        suffix, ids = parser.finish(completed=completed, suffix=self.route_suffix)
        return body + self._render(ids) + suffix

    def events(self, events):
        from .mc.stream import Delta, Finish, Failure
        if not self.offers:
            yield from events
            return
        parser = SelectionBuffer(self.nonce)
        failed = False
        for event in events:
            if isinstance(event, Delta):
                text = parser.feed(event.text)
                if text:
                    yield Delta(text)
            elif isinstance(event, Finish):
                suffix, ids = parser.finish(completed=not failed and event.reason == 'stop', suffix=self.route_suffix)
                tail = self._render(ids) + suffix
                if tail:
                    yield Delta(tail)
                yield event
            else:
                failed |= isinstance(event, Failure)
                yield event
