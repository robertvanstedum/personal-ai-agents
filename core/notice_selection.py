"""Bounded optional terminal presentation selection. Never grants action authority."""
from __future__ import annotations

import json

MARKER = '[[MINIMOI_NOTICES_V1]]'
MAX_BYTES = 4096
MAX_SELECTED = 3


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


class SelectionBuffer:
    """Stream ordinary prose; retain only a possible marker prefix or bounded control tail.

    Recognized malformed/oversized control is discarded, never rendered or retried.
    A selected notice is not presented until its host-rendered text is saved.
    """
    def __init__(self, nonce):
        self.nonce = nonce
        self.pending = ''
        self.control = False
        self.oversized = False
        self.closed = False
        self.has_answer = False

    def feed(self, text):
        if self.closed:
            raise ValueError('selection already finished')
        if self.oversized:
            return ''
        self.pending += text
        if not self.control:
            at = self.pending.find(MARKER)
            if at >= 0:
                out, self.pending = self.pending[:at], self.pending[at + len(MARKER):]
                self.control = True
            else:
                keep = next((n for n in range(min(len(MARKER)-1, len(self.pending)), 0, -1)
                             if self.pending.endswith(MARKER[:n])), 0)
                out = self.pending[:-keep] if keep else self.pending
                self.pending = self.pending[-keep:] if keep else ''
            self.has_answer |= bool(out.strip())
        else:
            out = ''
        if self.control and len(self.pending.encode()) > MAX_BYTES:
            self.pending, self.oversized = '', True
        return out

    def finish(self, *, completed, suffix=''):
        if self.closed:
            raise ValueError('selection already finished')
        self.closed = True
        if not self.control:
            return ('' if self.pending.startswith('[[MINIMOI') else self.pending), []
        selected = []
        if completed and self.has_answer and not self.oversized:
            try:
                obj, end = json.JSONDecoder(object_pairs_hook=unique).raw_decode(self.pending.lstrip())
                tail = self.pending.lstrip()[end:].strip()
                if (type(obj) is dict and set(obj) == {'nonce', 'ids'} and obj['nonce'] == self.nonce
                        and type(obj['ids']) is list and len(obj['ids']) <= MAX_SELECTED
                        and all(type(x) is str and len(x) <= 80 for x in obj['ids'])
                        and len(set(obj['ids'])) == len(obj['ids']) and tail == suffix.strip()):
                    selected = obj['ids']
            except (ValueError, TypeError):
                pass
        # A host-generated billing suffix may follow model control. Preserve it
        # even if control is invalid; no arbitrary trailing model prose escapes.
        return ('\n\n' + suffix.strip() if suffix and self.has_answer else ''), selected
