"""Pure proposed external-processing policy; no file reader, writer or runtime wiring.

The existing authenticated identity.Grant remains caller authority. A host may
supply a server-owned ProcessingContext and bytes of owner-controlled policy;
neither JSON structure nor owner_confirmation_ref proves owner authorization.
This parser/evaluator does not enroll callers, constrain provider routes, read
passages, isolate ranking or make a production endpoint safe. All inputs must
come from trusted host code, never model/body/header destination assertions.
Local/shared callers retain their existing path: this module is not wired in.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re

from core.memory_ask.identity import Grant, NAME as CALLER_NAME, RESERVED as RESERVED_CALLERS
from core.memory_index import policy

SCHEMA = 'minimoi.external-edition-policy.v1'
MAX_BYTES = 256 * 1024
LIMITS = {'destinations': 8, 'enrollments': 32, 'sources': 256, 'records': 512, 'editions': 2048}
_TOKEN = re.compile(r'[a-z][a-z0-9_-]{1,63}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_RECORD = re.compile(r'[0-9A-HJKMNP-TV-Z]{26}\Z')


class Refused(ValueError):
    """Fixed parser failure, with no policy/source text in the error."""


def need(ok):
    if not ok:
        raise Refused('processing_policy_invalid')


def text(value, maximum=256):
    need(type(value) is str and 0 < len(value) <= maximum
         and not any(ord(char) < 32 or ord(char) == 127 for char in value))
    return value


def match(value, pattern):
    need(type(value) is str and pattern.fullmatch(value) is not None)
    return value


def caller_name(value):
    match(value, CALLER_NAME)
    need(value not in RESERVED_CALLERS)
    return value


def revoked(value):
    if value is None:
        return False
    text(value, 32)
    need(re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', value) is not None)
    try:
        datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        raise Refused('processing_policy_invalid') from None
    return True


def unique(pairs):
    doc = {}
    for key, value in pairs:
        need(key not in doc)
        doc[key] = value
    return doc


def shape(row, keys):
    need(type(row) is dict and set(row) == set(keys))


@dataclass(frozen=True)
class ProcessingContext:
    """Trusted host binding only; profile revision identifies an enforced route elsewhere."""
    destination_id: str
    destination_revision_sha256: str
    policy_revision_sha256: str
    environment: str = 'production'

    def __post_init__(self):
        match(self.destination_id, _TOKEN)
        match(self.destination_revision_sha256, _SHA)
        match(self.policy_revision_sha256, _SHA)
        need(self.environment == 'production')


@dataclass(frozen=True)
class Snapshot:
    revision_sha256: str
    destinations: tuple[tuple[str, str], ...]
    enrollments: tuple[tuple[str, str, str, bool], ...]
    sources: tuple[tuple[str, bool], ...]
    records: tuple[tuple[str, str, bool], ...]
    editions: tuple[tuple[str, str, str, str, str, str, bool], ...]


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str  # Internal fixed reason only; a future remote adapter must return generic unavailable.


def parse(raw: bytes) -> Snapshot:
    """Bound bytes BEFORE decoding. Duplicate/unknown keys and unknown references refuse.

    Revision is the exact input-byte SHA256; even whitespace changes require a
    fresh host context. Returned nested state contains immutable tuples only.
    No path argument or real policy file is created or opened here.
    """
    need(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES)
    try:
        doc = json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
                         parse_constant=lambda _: need(False))
    except (ValueError, UnicodeError, RecursionError):
        raise Refused('processing_policy_invalid') from None
    shape(doc, ('schema', *LIMITS))
    need(doc['schema'] == SCHEMA)
    for key, maximum in LIMITS.items():
        need(type(doc[key]) is list and len(doc[key]) <= maximum)
    destinations = []
    for row in doc['destinations']:
        shape(row, ('id', 'revision_sha256'))
        destinations.append((match(row['id'], _TOKEN), match(row['revision_sha256'], _SHA)))
    need(len({name for name, _ in destinations}) == len(destinations))
    enrollments = []
    for row in doc['enrollments']:
        shape(row, ('caller', 'destination_id', 'destination_revision_sha256', 'revoked_at'))
        entry = (caller_name(row['caller']), match(row['destination_id'], _TOKEN),
                 match(row['destination_revision_sha256'], _SHA), revoked(row['revoked_at']))
        need(entry[1:3] in destinations)
        enrollments.append(entry)
    need(len({row[:3] for row in enrollments}) == len(enrollments))
    sources = []
    for row in doc['sources']:
        shape(row, ('source_id', 'local_only'))
        need(type(row['local_only']) is bool)
        sources.append((text(row['source_id']), row['local_only']))
    need(len({name for name, _ in sources}) == len(sources))
    records = []
    for row in doc['records']:
        shape(row, ('record_id', 'source_id', 'local_only'))
        need(type(row['local_only']) is bool)
        entry = (match(row['record_id'], _RECORD), text(row['source_id']), row['local_only'])
        need(entry[1] in {source[0] for source in sources})
        records.append(entry)
    need(len({row[0] for row in records}) == len(records))
    editions = []
    for row in doc['editions']:
        shape(row, ('caller', 'destination_id', 'destination_revision_sha256', 'record_id',
                    'edition_hash', 'owner_confirmation_ref', 'revoked_at'))
        entry = (caller_name(row['caller']), match(row['destination_id'], _TOKEN),
                 match(row['destination_revision_sha256'], _SHA), match(row['record_id'], _RECORD),
                 match(row['edition_hash'], _SHA), text(row['owner_confirmation_ref'], 128), revoked(row['revoked_at']))
        need(entry[:3] in {enrollment[:3] for enrollment in enrollments}
             and entry[3] in {record[0] for record in records})
        editions.append(entry)
    need(len({row[:5] for row in editions}) == len(editions))
    return Snapshot(hashlib.sha256(raw).hexdigest(), tuple(destinations), tuple(enrollments),
                    tuple(sources), tuple(records), tuple(editions))


def evaluate(snapshot: Snapshot | None, grant: Grant | None, context: ProcessingContext | None,
             meta: dict, *, current_revision_sha256: str | None = None, include_raw=False) -> Decision:
    """Evaluate supplied metadata only. Never materialize any evidence or call a reader.

    Host must freshly authenticate grant/load policy, supply VERIFIED edition
    metadata, re-evaluate before each main/reference read and response publish,
    and isolate the eligible corpus before ranking. current_revision_sha256 is
    a fresh server-owned value, not proof this module reads/reloads any policy.
    Deny/local-only never modifies local/shared grants or original records.
    """
    def deny(code):
        return Decision(False, code)
    if not isinstance(snapshot, Snapshot) or not isinstance(context, ProcessingContext):
        return deny('processing_context_missing')
    if current_revision_sha256 != snapshot.revision_sha256 or context.policy_revision_sha256 != snapshot.revision_sha256:
        return deny('processing_revision_changed')
    if not isinstance(grant, Grant) or grant.revoked is not False:
        return deny('caller_unavailable')
    destination = (context.destination_id, context.destination_revision_sha256)
    enrollment = (grant.name, *destination, False)
    if destination not in snapshot.destinations or enrollment not in snapshot.enrollments:
        return deny('destination_unavailable')
    if type(meta) is not dict or type(include_raw) is not bool:
        return deny('record_unavailable')
    if 'local_only' in meta:
        if type(meta['local_only']) is not bool:
            return deny('classification_unavailable')
        if meta['local_only']:
            return deny('local_only')
    try:
        if policy.record_gate(meta) or policy.allowed(meta, grant.caller(include_raw)):
            return deny('record_unavailable')
    except (ValueError, PermissionError, TypeError, AttributeError):
        return deny('record_unavailable')
    record_id, source_id, edition_hash = meta.get('id'), meta.get('source'), meta.get('edition_hash')
    record_rows = [row for row in snapshot.records if row[0] == record_id]
    source_rows = [row for row in snapshot.sources if row[0] == source_id]
    if len(record_rows) != 1 or len(source_rows) != 1 or record_rows[0][1] != source_id:
        return deny('classification_unavailable')
    if record_rows[0][2] or source_rows[0][1]:
        return deny('local_only')
    exact = (grant.name, *destination, record_id, edition_hash)
    if not any(row[:5] == exact and row[6] is False for row in snapshot.editions):
        return deny('edition_unavailable')
    return Decision(True, 'allowed')
