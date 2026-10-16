"""Admission budgets for an explicit Codex refresh, never truncation or an RSS guarantee.

Accepted lines are returned unchanged to the existing parser and coverage counter.
Limits fail the entire source before a bundle is staged. Transient JSON expansion
and Python allocator overhead still require an isolated memory-capped rehearsal.
"""
from dataclasses import dataclass, fields
import json
from core.memory_shelf import sessions

CODES = frozenset({'source_line_limit', 'source_row_limit', 'source_text_limit',
                   'source_entry_limit', 'source_parts_limit', 'source_identity_mismatch'})

class ResourceLimit(ValueError):
    def __init__(self, code):
        if code not in CODES: raise ValueError('invalid_resource_reason')
        self.code = code
        super().__init__(code)

@dataclass(frozen=True)
class CodexLimits:
    line_bytes: int = 32 << 20
    rows: int = 100000
    retained_bytes: int = 16 << 20
    entries: int = 100000
    parts_per_message: int = 1024

    def __post_init__(self):
        if any(type(getattr(self, f.name)) is not int or getattr(self, f.name) <= 0 for f in fields(self)):
            raise ValueError('invalid_codex_limits')

class _Budget:
    def __init__(self, limits):
        self.limits, self.text_bytes, self.entries = limits, 0, 0

    def charge(self, text_bytes=0, entries=0):
        self.text_bytes += text_bytes
        self.entries += entries
        if self.text_bytes > self.limits.retained_bytes: raise ResourceLimit('source_text_limit')
        if self.entries > self.limits.entries: raise ResourceLimit('source_entry_limit')

    def message(self, parts, phase=None):
        # Same format-only extraction used by the real parser; no prose attribution.
        if parts and hasattr(parts, '__len__') and len(parts) > self.limits.parts_per_message:
            raise ResourceLimit('source_parts_limit')
        msg = sessions._codex_message(1, 'copy', 'user', phase, 0, parts)
        strings = [msg.text, msg.phase or '', *msg.injected, *msg.other]
        self.charge(sum(len(x.encode('utf-8')) for x in strings), 4 + 2 * (len(msg.injected) + len(msg.other)))

    def handoff(self, payload, raw_bytes):
        # The current parser retains the complete handoff dictionary until reconciliation.
        parts = payload.get('content')
        if parts and hasattr(parts, '__len__') and len(parts) > self.limits.parts_per_message:
            raise ResourceLimit('source_parts_limit')
        self.tree(payload, raw_bytes)

    def tree(self, payload, raw_bytes):
        self.charge(raw_bytes)
        stack = [iter((payload,))]
        while stack:
            try: value = next(stack[-1])
            except StopIteration:
                stack.pop(); continue
            self.charge(entries=1)
            if isinstance(value, dict):
                self.charge(entries=len(value))  # dictionary keys are retained too
                stack.append(iter(value.values()))
            elif isinstance(value, list): stack.append(iter(value))


def checked_codex_lines(lines, limits, expected_source_id=None):
    """Yield identical raw lines; retain only counters between yields, never decoded rows."""
    if not isinstance(limits, CodexLimits): raise ValueError('invalid_codex_limits')
    budget, seen_identity = _Budget(limits), False
    for number, raw in enumerate(lines, 1):
        if number > limits.rows: raise ResourceLimit('source_row_limit')
        raw_bytes = len(raw) if isinstance(raw, bytes) else len(raw.encode('utf-8'))
        if raw_bytes > limits.line_bytes: raise ResourceLimit('source_line_limit')
        budget.charge(entries=2)  # source event + eventual omission pointer/turn
        try: row = json.loads(raw)
        except (ValueError, UnicodeError): row = None
        if isinstance(row, dict):
            kind, payload = row.get('type'), row.get('payload')
            # Skip labels and timestamps can also survive in Parsed/events.
            budget.charge(len(str(kind).encode('utf-8')))
            timestamp = row.get('timestamp')
            if isinstance(timestamp, str): budget.charge(len(timestamp.encode('utf-8')))
            elif timestamp is not None: budget.tree(timestamp, raw_bytes)
            if isinstance(payload, dict):
                sub = payload.get('type')
                budget.charge(len(str(sub).encode('utf-8')))
                if kind == 'session_meta':
                    if expected_source_id is not None and payload.get('id') != expected_source_id:
                        raise ResourceLimit('source_identity_mismatch')
                    seen_identity = True
                elif kind == 'response_item' and sub == 'agent_message':
                    budget.handoff(payload, raw_bytes)
                elif kind == 'event_msg' and sub == 'item_completed':
                    item = payload.get('item') or {}
                    if isinstance(item, dict) and item.get('type') in ('UserMessage', 'AgentMessage'):
                        budget.message(item.get('content'), item.get('phase'))
                    elif isinstance(item, dict):
                        budget.charge(len(str(item.get('type')).encode('utf-8')))
                elif kind == 'response_item' and sub == 'message':
                    if payload.get('role') in ('user', 'assistant'):
                        budget.message(payload.get('content'), payload.get('phase'))
            # Release potentially large decoded JSON before coverage and parser decode the line.
            item = timestamp = None  # release decoded metadata before yielding
            del payload
        del row
        yield raw
    if expected_source_id is not None and not seen_identity:
        raise ResourceLimit('source_identity_mismatch')
