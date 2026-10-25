"""Bounded persisted-history reader. This client cannot start/resume/steer a turn.

The native binary and home are operator bindings, never tool arguments. A
notLoaded thread describes this observer, not whether the desktop worker is busy.
Only the returned narrow projection is eligible for durable receipt evidence.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import selectors
import subprocess
import time

from .codex_desktop_queue import BUNDLED_BINARY, _binary
from .direct_contact import Refused, Target, _hash

METHODS = frozenset({'initialize', 'thread/read', 'thread/turns/list', 'thread/items/list'})
MAX_BYTES = 4 * 1024 * 1024
MAX_LINE = 512 * 1024
ITEM_PAGE_SIZE = 10
MAX_ITEM_PAGES = 160  # Preserve the original 16 * 100 item ceiling.


def need(condition, reason='native_history_unavailable'):
    if not condition:
        raise Refused(reason)


@dataclass(frozen=True)
class ReadEnrollment:
    target: Target
    binary_identity: tuple
    codex_home: str

    def __post_init__(self):
        need(isinstance(self.target, Target) and self.target.recipient == 'codex'
             and self.target.host == 'local', 'unsupported_target')
        need(type(self.binary_identity) is tuple and len(self.binary_identity) == 8
             and all(type(x) is int and x >= 0 for x in self.binary_identity), 'invalid_binary_binding')
        need(type(self.codex_home) is str and self.codex_home.startswith('/')
             and '..' not in Path(self.codex_home).parts, 'invalid_native_home')


class StdioReader:
    def __init__(self, enrollment, *, timeout=15, popen=subprocess.Popen):
        need(isinstance(enrollment, ReadEnrollment), 'enrollment_required')
        need(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 20,
             'invalid_timeout')
        self.enrollment, self.timeout, self.popen = enrollment, timeout, popen

    @contextmanager
    def session(self):
        e = self.enrollment
        need(_binary(BUNDLED_BINARY) == e.binary_identity, 'observer_binary_changed')
        # Avoid inheriting executor tokens, provider keys, PYTHONPATH or alternate
        # app-server endpoints. The fixed home remains the existing desktop home.
        env = {'HOME': str(Path(e.codex_home).parent), 'CODEX_HOME': e.codex_home,
               'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8'}
        p = self.popen([BUNDLED_BINARY, 'app-server', '--listen', 'stdio://'],
                       cwd=e.target.cwd, env=env, stdin=subprocess.PIPE,
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        selector = selectors.DefaultSelector()
        selector.register(p.stdout, selectors.EVENT_READ)
        deadline, serial, total, buffer = time.monotonic() + self.timeout, 0, 0, b''
        def call(method, params):
            nonlocal serial, total, buffer
            need(method in METHODS, 'native_method_forbidden')
            serial += 1
            p.stdin.write((json.dumps({'id': serial, 'method': method, 'params': params}) + '\n').encode())
            p.stdin.flush()
            while True:
                need(time.monotonic() < deadline, 'native_read_timeout')
                if b'\n' not in buffer:
                    need(len(buffer) <= MAX_LINE, 'native_read_bound')
                    need(selector.select(max(0, deadline - time.monotonic())), 'native_read_timeout')
                    chunk = os.read(p.stdout.fileno(), 65536)
                    need(chunk, 'native_read_eof')
                    total += len(chunk)
                    need(total <= MAX_BYTES, 'native_read_bound')
                    buffer += chunk
                    continue
                raw, buffer = buffer.split(b'\n', 1)
                need(len(raw) <= MAX_LINE, 'native_read_bound')
                try:
                    message = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise Refused('native_read_invalid') from None
                need(type(message) is dict)
                # Never answer a server request, approval, or tool execution.
                need(not ('method' in message and 'id' in message), 'native_server_request_refused')
                if 'id' not in message:
                    continue
                need(message.get('id') == serial and 'error' not in message
                     and type(message.get('result')) is dict)
                return message['result']
        try:
            initialized = call('initialize', {'clientInfo': {'name': 'minimoi_read_observer', 'version': '1'},
                                              'capabilities': {'experimentalApi': True}})
            need(initialized.get('codexHome') == e.codex_home, 'native_home_changed')
            p.stdin.write(b'{"method":"initialized","params":{}}\n')
            p.stdin.flush()
            yield call
            need(_binary(BUNDLED_BINARY) == e.binary_identity, 'observer_binary_changed')
        finally:
            selector.close()
            # Reap this reader only. No thread interrupt/cancel or descendant kill.
            if p.poll() is None:
                p.terminate()
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=2)
            p.stdin.close()
            p.stdout.close()


class Observer:
    def __init__(self, enrollment, *, reader=None):
        self.enrollment = enrollment
        self.reader = reader or StdioReader(enrollment)

    def snapshot(self, baseline=None):
        """Baseline: latest turn only. Later: every turn through that same boundary.

        Refresh all items in the boundary turn: steering may append there. Bounds
        refuse the observation, never silently truncate a receipt search.
        """
        e, turns, seen = self.enrollment, [], set()
        if baseline is not None:
            need(baseline.get('target_revision') == e.target.revision
                 and baseline.get('thread_id') == e.target.session_id, 'baseline_binding_changed')
        with self.reader.session() as call:
            thread = call('thread/read', {'threadId': e.target.session_id}).get('thread', {})
            need(thread.get('id') == e.target.session_id and thread.get('cwd') == e.target.cwd,
                 'native_thread_binding_changed')
            cursor, found = None, False
            for _ in range(8):
                args = {'threadId': e.target.session_id, 'limit': 20 if baseline else 1,
                        'sortDirection': 'desc', 'itemsView': 'notLoaded'}
                if cursor:
                    args['cursor'] = cursor
                page = call('thread/turns/list', args)
                need(type(page.get('data')) is list and len(page['data']) <= args['limit'])
                for turn in page['data']:
                    need(type(turn) is dict and type(turn.get('id')) is str and 0<len(turn['id'])<=128 and turn['id'] not in seen)
                    seen.add(turn['id'])
                    projection = {k: turn.get(k) for k in ('id', 'status', 'startedAt', 'completedAt')}
                    projection['items'] = self._items(call, e.target.session_id, turn['id'])
                    turns.append(projection)
                    if baseline is None or turn['id'] == baseline.get('boundary_turn'):
                        found = True
                        break
                if found or page.get('nextCursor') is None:
                    break
                next_cursor = page.get('nextCursor')
                need(type(next_cursor) is str and 0<len(next_cursor)<=4096 and next_cursor != cursor)
                cursor = next_cursor
            else:
                raise Refused('native_history_page_bound')
            if baseline and baseline.get('boundary_turn') is not None:
                need(found, 'native_boundary_missing')
        return {'thread_id': e.target.session_id, 'target_revision': e.target.revision,
                'observed_at': time.time(), 'turns': turns}

    @staticmethod
    def _items(call, thread, turn):
        # Small wire pages prevent ordinary tool output batches from exceeding
        # the unchanged per-line cap. All pages/IDs remain mandatory; aggregate
        # byte and session-time bounds still refuse incomplete observations.
        cursor, items, seen = None, [], set()
        for _ in range(MAX_ITEM_PAGES):
            args = {'threadId': thread, 'turnId': turn, 'limit': ITEM_PAGE_SIZE, 'sortDirection': 'asc'}
            if cursor:
                args['cursor'] = cursor
            page = call('thread/items/list', args)
            need(type(page.get('data')) is list and len(page['data']) <= ITEM_PAGE_SIZE)
            for entry in page['data']:
                need(type(entry) is dict and entry.get('turnId') == turn)
                item = entry.get('item')
                need(type(item) is dict and type(item.get('id')) is str and 0<len(item['id'])<=256 and item['id'] not in seen)
                seen.add(item['id'])
                # Tool outputs/reasoning cannot act as acknowledgments; retain
                # only their identity/hash to detect source changes, not content.
                row = {'id': item['id'], 'type': item.get('type'), 'sha256': _hash(item)}
                if item.get('type') == 'userMessage':
                    content = item.get('content')
                    if (type(content) is list and len(content) == 1 and type(content[0]) is dict
                            and content[0].get('type') == 'text' and type(content[0].get('text')) is str):
                        row['text'] = content[0]['text']
                elif item.get('type') == 'agentMessage':
                    need(type(item.get('text')) is str)
                    row.update(text=item['text'], phase=item.get('phase'))
                items.append(row)
            if page.get('nextCursor') is None:
                return items
            next_cursor = page.get('nextCursor')
            need(type(next_cursor) is str and 0<len(next_cursor)<=4096 and next_cursor != cursor)
            cursor = next_cursor
        raise Refused('native_history_page_bound')


def baseline_for(snapshot):
    turns = snapshot['turns']
    return {'thread_id': snapshot['thread_id'], 'target_revision': snapshot['target_revision'],
            'boundary_turn': turns[0]['id'] if turns else None,
            # User inputs are immutable. Active tool/streaming/reasoning items
            # legitimately evolve; retain their IDs to exclude them as replies,
            # but do not pin mutable content as if it were a completed snapshot.
            'items': {i['id']: i['sha256'] if i.get('type')=='userMessage' else None
                      for t in turns for i in t['items']}}


def correlate(snapshot, baseline, exact_message, correlation):
    """Pure validation of trusted native data; strings alone aren't authentication."""
    need(snapshot['thread_id'] == baseline['thread_id'] and
         snapshot['target_revision'] == baseline['target_revision'], 'baseline_binding_changed')
    observed_ids = {i['id'] for t in snapshot['turns'] for i in t['items']}
    need(set(baseline['items']) <= observed_ids, 'native_source_missing')
    matches = []
    for turn in snapshot['turns']:
        for n, item in enumerate(turn['items']):
            if item['id'] in baseline['items']:
                if baseline['items'][item['id']] is not None:
                    need(item['sha256'] == baseline['items'][item['id']], 'native_source_changed')
                continue
            if item['type'] == 'userMessage' and item.get('text') == exact_message:
                matches.append((turn, n, item))
    need(len(matches) <= 1, 'native_duplicate_input')
    if not matches:
        return {'state': 'unverified', 'reason': 'native_input_not_found'}
    turn, at, source = matches[0]
    proof = {'turn_id': turn['id'], 'input_id': source['id'], 'input_sha256': source['sha256']}
    answers = [i for i in turn['items'][at + 1:] if i['type'] == 'agentMessage' and
               i['id'] not in baseline['items']]
    finals = [i for i in answers if i.get('phase') == 'final_answer' and
              i.get('text', '').startswith('MC-RESULT:' + correlation + '\n')]
    need(len(finals) <= 1, 'native_duplicate_result')
    if finals:
        start, end = turn.get('startedAt'), turn.get('completedAt')
        if (turn['status'] == 'completed' and type(start) in (int, float) and
                type(end) in (int, float) and math.isfinite(start) and math.isfinite(end)
                and 0 < start <= end <= snapshot['observed_at'] + 5):
            final = finals[0]
            # Explicit correlation, role, final phase, same turn and chronology;
            # this is a native report, never semantic acceptance of its claims.
            return {'state': 'result', **proof, 'result_id': final['id'],
                    'result_sha256': final['sha256'], 'completed_at': end,
                    'report': final['text'].split('\n', 1)[1]}
    acks = [i for i in answers if ('MC-ACK:' + correlation) in i.get('text', '').splitlines()]
    if acks:
        return {'state': 'acknowledged', **proof, 'ack_id': acks[0]['id'], 'ack_sha256': acks[0]['sha256']}
    return {'state': 'delivered', **proof, 'reason': 'native_input_persisted_not_pickup'}
