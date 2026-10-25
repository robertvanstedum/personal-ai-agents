"""Read-only, checkpointed task reports. No contact/queue authority exists here."""
import hashlib
import json
import math
import re
import time
from .codex_read_observer import Observer, need

MAX_STATE = 512 * 1024
CHUNK = 6000
DONE = frozenset({'complete', 'ready_for_owner_uat'})
OUTCOMES = DONE | {'blocked', 'refused'}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def sha(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def validate_spec(spec):
    keys = {'task_id', 'generation', 'scope_sha256', 'correlation'}
    need(type(spec) is dict and set(spec) == keys, 'task_spec_invalid')
    need(all(type(spec[k]) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', spec[k])
             for k in ('task_id', 'generation')), 'task_spec_invalid')
    need(all(type(spec[k]) is str and re.fullmatch('[0-9a-f]{64}', spec[k])
             for k in ('scope_sha256', 'correlation')), 'task_spec_invalid')


def identities(items):
    return {i['id']: i['sha256'] if i['type'] == 'userMessage' else None for i in items}


def unchanged(old, items):
    current = {i['id']: i for i in items}
    need(set(old) <= set(current), 'task_source_missing')
    for key, digest in old.items():
        if digest is not None:
            need(current[key]['sha256'] == digest, 'task_source_changed')


def sealed_terminal(metadata):
    """Interrupted with no completion time is provisional native metadata."""
    return metadata['status'] in ('completed','failed') or (metadata['status']=='interrupted' and metadata['completedAt'] is not None)


def anchor_lifecycle(previous,current,*,now=None):
    """Pure pinned-native compatibility check; does not waive item verification.

    Only observed interrupted/null -> completed is newly supported. IDs/start
    remain exact; completed/failed/non-null interrupted metadata stays sealed.
    Callers must reread every item and preserve registration exclusions.
    """
    need(previous['id']==current['id'] and previous['startedAt']==current['startedAt'],'task_anchor_changed')
    if current==previous:return 'unchanged'
    if previous['status']=='interrupted' and previous['completedAt'] is None:
        start,end=current['startedAt'],current['completedAt']
        now=time.time() if now is None else now
        need(current['status']=='completed' and all(type(t) in (int,float) and math.isfinite(t) for t in (start,end,now))
             and 0<start<=end<=now+5,'task_anchor_changed')
        return 'reconciled'
    need(not sealed_terminal(previous),'task_anchor_changed')
    return 'reconciled'


def report(turn, excluded, spec, now):
    candidates = []
    for item in turn['items']:
        if item['id'] in excluded or item['type'] != 'agentMessage' or item.get('phase') != 'final_answer':
            continue
        text = item.get('text', '')
        if not text.startswith('{'):
            continue
        try:
            value, _ = json.JSONDecoder(object_pairs_hook=unique).raw_decode(text)
        except (ValueError, TypeError):
            continue
        if type(value) is not dict or value.get('format') != 'minimoi.task-terminal.v1':
            continue
        need(set(value) == {'format', *spec.keys(), 'outcome', 'evidence_refs', 'remaining'}, 'task_report_invalid')
        need(value['outcome'] in OUTCOMES and type(value['remaining']) is str and len(value['remaining']) <= 2000,
             'task_report_invalid')
        refs = value['evidence_refs']
        need(type(refs) is list and len(refs) <= 8 and all(type(r) is str and
             re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', r) for r in refs), 'task_report_invalid')
        validate_spec({k:value[k] for k in spec})
        # A shared enrolled chat can report several independent subscriptions.
        # Only this task/generation owns the scope/token comparison.
        if value['task_id'] != spec['task_id'] or value['generation'] != spec['generation']:
            continue
        need(all(value.get(k) == v for k, v in spec.items()), 'task_report_binding_changed')
        start, end = turn['startedAt'], turn['completedAt']
        need(turn['status'] == 'completed' and all(type(t) in (int, float) and math.isfinite(t) for t in (start, end))
             and 0 < start <= end <= now + 5, 'task_report_chronology')
        candidates.append({'report': value, 'turn_id': turn['id'], 'item_id': item['id'],
                           'item_sha256': item['sha256'], 'completed_at': end})
    need(len(candidates) <= 1, 'task_conflicting_reports')
    return candidates[0] if candidates else None


def unique(pairs):
    result = {}
    for k, v in pairs:
        if k in result: raise ValueError('duplicate key')
        result[k] = v
    return result


class TaskObserver(Observer):
    def _bound(self, call):
        t = self.enrollment.target
        thread = call('thread/read', {'threadId': t.session_id}).get('thread', {})
        need(thread.get('id') == t.session_id and thread.get('cwd') == t.cwd, 'native_thread_binding_changed')

    def metadata(self, anchor, *, initial=False):
        """Freeze newest batch head, find mandatory anchor; never jump over a gap."""
        rows, seen, cursors, cursor = [], set(), set(), None
        with self.reader.session() as call:
            self._bound(call)
            for _ in range(8):
                args = {'threadId': self.enrollment.target.session_id, 'limit': 1 if initial else 20,
                        'sortDirection': 'desc', 'itemsView': 'notLoaded'}
                if cursor: args['cursor'] = cursor
                page = call('thread/turns/list', args)
                need(type(page.get('data')) is list and len(page['data']) <= args['limit'])
                for row in page['data']:
                    need(type(row) is dict and type(row.get('id')) is str and 0 < len(row['id']) <= 128
                         and row['id'] not in seen, 'task_duplicate_turn')
                    seen.add(row['id'])
                    rows.append({k: row.get(k) for k in ('id', 'status', 'startedAt', 'completedAt')})
                    if initial or row['id'] == anchor:
                        return rows
                cursor = page.get('nextCursor')
                if cursor is None:
                    need(anchor is None, 'task_history_gap')
                    return rows
                need(type(cursor) is str and 0 < len(cursor) <= 4096 and cursor not in cursors, 'task_cursor_repeated')
                cursors.add(cursor)
        need(False, 'task_history_page_bound')

    def read_turn(self, metadata):
        with self.reader.session() as call:
            self._bound(call)
            return dict(metadata, items=self._items(call, self.enrollment.target.session_id, metadata['id']))

    def step(self, state, spec):
        """At most anchor + one next turn, each in its own bounded reader session.

        A complete checkpoint is returned only after all pages of that turn.
        Only registration-baseline IDs are ineligible as new reports. Later
        streaming items retain eligibility after their turn completes.
        """
        validate_spec(spec)
        target = self.enrollment.target
        binding = {'thread_id': target.session_id, 'revision': target.revision, 'spec_sha256': sha(spec)}
        if state is None:
            rows = self.metadata(None, initial=True)
            turn = self.read_turn(rows[0]) if rows else None
            new = dict(binding, anchor=rows[0] if rows else None,
                       items=identities(turn['items']) if turn else {}, excluded=sorted(identities(turn['items'])) if turn else [],
                       active=None, batch_head=None, pending_done=None)
            need(len(encoded(new)) <= MAX_STATE, 'task_checkpoint_bound')
            return {'checkpoint': new, 'proof': None, 'state': 'armed'}
        need(all(state.get(k) == v for k, v in binding.items()), 'task_checkpoint_binding_changed')
        anchor = state['anchor']
        rows = self.metadata(anchor['id'] if anchor else None)
        new, proof = dict(state), None
        if state['batch_head'] is not None:
            indexes=[n for n,r in enumerate(rows) if r['id']==state['batch_head']]
            need(len(indexes)==1, 'task_batch_head_missing')
            rows=rows[indexes[0]:]  # arrivals after the frozen head belong to the next batch
        elif rows and (anchor is None or rows[0]['id']!=anchor['id'] or anchor['status'] not in ('completed','failed','interrupted')):
            new['batch_head']=rows[0]['id']
        if anchor:
            current = rows[-1]
            lifecycle=anchor_lifecycle(anchor,current)
            turn = self.read_turn(current)  # complete bounded reread, even reconciled anchors
            unchanged(state['items'], turn['items'])
            if anchor['status'] not in ('completed', 'failed', 'interrupted') or lifecycle=='reconciled':
                if new['batch_head'] is None:new['batch_head']=rows[0]['id']
                return self._advance(new, turn, state['excluded'], spec, initial_anchor=True)
            rows = rows[:-1]
        if rows:
            turn = self.read_turn(rows[-1])  # oldest unseen, never skip an active hole
            if anchor:
                need(type(turn['startedAt']) in (int,float) and type(anchor['startedAt']) in (int,float)
                     and turn['startedAt']>=anchor['startedAt'], 'task_turn_chronology')
                need(not set(identities(turn['items'])) & set(state['items']), 'task_duplicate_item')
            active = state['active']
            if active:
                need(active['id'] == turn['id'] and active['startedAt'] == turn['startedAt'], 'task_active_changed')
                unchanged(active['items'], turn['items'])
            return self._advance(new, turn, state['excluded'], spec)
        return {'checkpoint': new, 'proof': proof, 'state': 'armed'}

    @staticmethod
    def _advance(state, turn, excluded, spec, initial_anchor=False):
        terminal = turn['status'] in ('completed', 'failed', 'interrupted')
        proof = report(turn, excluded, spec, time.time()) if turn['status'] == 'completed' else None
        metadata = {k: turn[k] for k in ('id', 'status', 'startedAt', 'completedAt')}
        if proof is not None:
            need(state['pending_done'] is None, 'task_conflicting_reports')
            if proof['report']['outcome'] in DONE:
                state['pending_done']=proof
                proof=None
        if terminal:
            state.update(anchor=metadata, items=identities(turn['items']), active=None)
        elif initial_anchor:
            state.update(anchor=metadata, items=identities(turn['items']))
        else:
            state['active'] = dict(id=turn['id'], startedAt=turn['startedAt'], items=identities(turn['items']))
        if terminal and turn['id']==state['batch_head']:
            proof=state['pending_done'] or proof
            state.update(batch_head=None,pending_done=None)
        need(len(encoded(state)) <= MAX_STATE, 'task_checkpoint_bound')
        return {'checkpoint': state, 'proof': proof, 'state': 'armed'}
