"""Private named working documents on the canonical Workshop journal.

The host constructs Binding from authenticated context, never model arguments.
A revision is a committed event plus retained bytes, not a mutable file or index hit.
Both leads use the same principal/environment/scope partition. Drafts grant no authority.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import uuid

from core.workshop_journal import artifacts, schema, workflow
from core.workshop_journal.errors import PolicyRefused
from core.workshop_journal.journal import Journal

ACTION = 'save_working_artifact'
ID = re.compile(r'wa-[0-9a-f]{32}')
MAX_TEXT = 24000


@dataclass(frozen=True)
class Binding:
    principal: str
    environment: str
    scope: str
    lead: str
    source_turn: str

    def __post_init__(self):
        if self.lead not in ('mc', 'cos'):
            raise ValueError('unknown lead')
        for value in (self.principal, self.environment, self.scope, self.source_turn):
            if not isinstance(value, str) or not value or len(value) > 120 or not re.fullmatch(r'[A-Za-z0-9_.:@/\-]+', value):
                raise ValueError('invalid host binding')


def _rows(events):
    return [e for e in events if e.get('kind') == 'progress'
            and e.get('payload', {}).get('action') == ACTION]


def _ref(event):
    refs = [r for r in event['refs'] if r['type'] == 'artifact']
    if len(refs) != 1 or refs[0]['edition'] is None:
        raise ValueError('invalid working document record')
    return refs[0]


def _heads(events):
    heads = {}
    for event in _rows(events):
        heads[event['item'][5:]] = event
    return heads


class Store:
    def __init__(self, root, binding, *, validate_source, journal_factory=Journal):
        self.binding, self.validate_source = binding, validate_source
        # No caller-selected filesystem path or unscoped shared name.
        partition = json.dumps([binding.principal, binding.environment, binding.scope], separators=(',', ':'))
        self.partition = 'wa-' + hashlib.sha256(partition.encode()).hexdigest()[:28]
        Path(root).mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root, self.journal_factory = str(root), journal_factory

    def _journal(self, rules=workflow.check):
        return self.journal_factory(self.root, self.partition,
                                    actors=tuple(dict.fromkeys((*schema.DEFAULT_ACTORS, 'cos'))), rules=rules)

    def _events(self):
        self.validate_source(self.binding)
        read = self._journal().read()
        if read.status == 'missing':
            return []
        if not read.complete:
            raise RuntimeError('canonical_work_store_unavailable:' + read.status)
        return read.events

    @staticmethod
    def _summary(event):
        ref = _ref(event)
        return {'artifact_id': event['item'][5:], 'title': event['text'],
                'revision': ref['edition'], 'sha256': ref['sha256'],
                'author': event['actor'], 'event_id': event['event_id'],
                'saved_at': event['recorded_at'], 'authority': 'working_draft'}

    def find(self, query):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            raise ValueError('invalid query')
        matches = [self._summary(e) for e in _heads(self._events()).values()
                   if query.casefold() in e['text'].casefold()]
        matches.sort(key=lambda e: (e['title'].casefold(), e['artifact_id']))
        return {'items': matches[:20], 'more': len(matches) > 20,
                'ambiguous': len(matches) > 1, 'source': 'canonical', 'indexed_revision': None}

    def read(self, artifact_id, revision=None):
        if not isinstance(artifact_id, str) or not ID.fullmatch(artifact_id):
            raise ValueError('invalid artifact id')
        if revision is not None and (type(revision) is not int or revision < 1):
            raise ValueError('invalid revision')
        rows = [e for e in _rows(self._events()) if e['item'] == 'spec:' + artifact_id]
        if not rows:
            return {'status': 'not_found', 'source': 'canonical'}
        current = rows[-1]
        selected = next((e for e in rows if _ref(e)['edition'] == revision), None) if revision else current
        if selected is None:
            return {'status': 'revision_not_found', 'current_revision': _ref(current)['edition']}
        doc = json.loads(self._journal().open_artifact(_ref(selected)['sha256']))
        # Canonical bytes contain the host provenance, which must match this partition.
        b = self.binding
        if doc['binding'] != {'principal': b.principal, 'environment': b.environment, 'scope': b.scope}:
            raise PermissionError('artifact scope mismatch')
        return {**self._summary(selected), 'status': 'found', 'content': doc['content'],
                'unresolved': doc['unresolved'], 'source_turn': doc['source_turn'],
                'superseded': selected != current, 'current_revision': _ref(current)['edition'],
                'previous_revision': doc['previous_revision'], 'source': 'canonical',
                'indexed_revision': None, 'untrusted_data': True}

    def save(self, *, artifact_id, expected_revision, title, content, unresolved, operation_id):
        b = self.binding
        self.validate_source(b)
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 200:
            raise ValueError('invalid operation id')
        if type(expected_revision) is not int or not 0 <= expected_revision < 1000000:
            raise ValueError('invalid expected revision')
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160:
            raise ValueError('invalid title')
        if not isinstance(content, str) or not 1 <= len(content) <= MAX_TEXT:
            raise ValueError('invalid content')
        if not isinstance(unresolved, list) or len(unresolved) > 12 or any(
                not isinstance(q, str) or not 1 <= len(q) <= 300 for q in unresolved):
            raise ValueError('invalid unresolved questions')
        event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, self.partition + ':' + operation_id))
        if artifact_id is None:
            if expected_revision != 0:
                raise ValueError('new artifact requires revision zero')
            artifact_id = 'wa-' + uuid.UUID(event_id).hex
        if not isinstance(artifact_id, str) or not ID.fullmatch(artifact_id):
            raise ValueError('invalid artifact id')
        # Scrub field values before serialization: text regexes must never eat JSON punctuation.
        kept_content = artifacts.prepare(content.encode(), source_class='conversation')
        kept_questions = [artifacts.prepare(q.encode(), source_class='conversation') for q in unresolved]
        content = kept_content.data.decode()
        unresolved = [q.data.decode() for q in kept_questions]
        redactions = kept_content.redactions + sum(q.redactions for q in kept_questions)
        doc = {'binding': {'principal': b.principal, 'environment': b.environment, 'scope': b.scope},
               'content': content, 'unresolved': unresolved, 'source_turn': b.source_turn,
               'author': b.lead, 'previous_revision': expected_revision, 'authority': 'working_draft'}
        prepared = artifacts.prepare(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode(), source_class='conversation')
        if json.loads(prepared.data) != doc:
            raise ValueError('retained document changed its structure or provenance')
        ref = prepared.ref(artifact_id)
        ref['edition'] = expected_revision + 1
        envelope = {'event_id': event_id, 'actor': b.lead, 'kind': 'progress', 'item': 'spec:' + artifact_id,
                    'stage': 'design', 'text': title, 'refs': [ref, {'type': 'source', 'id': b.source_turn}],
                    'payload': {'action': ACTION}}

        def guarded(intent, events, resolver):
            workflow.check(intent, events, resolver)
            self.validate_source(b)
            head = _heads(events).get(artifact_id)
            actual = _ref(head)['edition'] if head else 0
            if actual != expected_revision:
                raise PolicyRefused('artifact_revision_conflict', evidence={'current_revision': actual})

        result = self._journal(guarded).append(envelope, adapter='work-artifacts',
                                                session_ref=b.source_turn, basis='adapter', artifacts=[prepared])
        if not result.ok:
            return {'status': result.status, 'reason': result.reason, 'committed': result.committed,
                    'evidence': result.evidence, 'artifact_id': artifact_id}
        # Read the saved revision, not a concurrent successor, for a truthful receipt.
        try:
            saved = self.read(artifact_id, expected_revision + 1)
        except Exception:
            return {'status': 'saved_readback_unavailable', 'committed': True,
                    'artifact_id': artifact_id, 'revision': expected_revision + 1,
                    'event_id': event_id, 'sha256': prepared.retained_sha256}
        return {**saved, 'status': 'saved', 'duplicate': result.status == 'duplicate',
                'redactions': redactions + prepared.redactions}
