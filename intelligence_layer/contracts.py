"""Offline contracts. Importing this module performs no IO or runtime startup."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

VERSION = 'minimoi.intelligence/1.0'
CLASSES = ('team', 'owner-local', 'owner-only')
TYPES = ('designation', 'source', 'relay', 'observation', 'candidate', 'lesson', 'decision', 'practice', 'outcome')
RELATIONS = ('motivates', 'alternative_to', 'evidence_for', 'evidence_against', 'corrects', 'supersedes', 'applies', 'outcome_of', 'relayed_from')


class ContractError(ValueError):
    """Safe diagnostics never include input content."""


class Unavailable(ContractError):
    pass


def canonical(value):
    def check(v):
        if v is None or type(v) in (str, int, bool):
            return
        if type(v) is list:
            for item in v:
                check(item)
            return
        if type(v) is dict and all(type(k) is str for k in v):
            for item in v.values():
                check(item)
            return
        raise ContractError('Only JSON integers, strings, booleans, null, arrays and objects are supported')
    check(value)
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def record_hash(record):
    return digest(canonical({k: v for k, v in record.items() if k != 'hash'}))


def seal(record):
    return {**record, 'hash': record_hash(record)}


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z', value):
        raise ContractError('Timestamp must be an explicit UTC ISO string')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ContractError('Invalid UTC timestamp') from None


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def stable_id(identity):
    # ULID syntax, with deterministic 128-bit digest payload; not time-sortable.
    # Clocks are explicit fields and must never be inferred from this identifier.
    number = int(digest(canonical(identity))[:32], 16)
    alphabet = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
    return ''.join(alphabet[(number >> (5 * i)) & 31] for i in reversed(range(26)))


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ContractError('Duplicate JSON key')
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs)
        canonical(value)
        return value
    except (ValueError, UnicodeError, TypeError):
        raise ContractError('Invalid canonical-compatible JSON') from None


def _validate(schema, value):
    """Bounded validator for the shipped JSON Schema subset; no refs/network.

    Reject unsupported keywords, so extending schemas cannot silently weaken
    validation. This is not a replacement for general-purpose jsonschema.
    """
    supported = {'$schema', 'title', 'description', 'type', 'enum', 'const', 'properties', 'required',
                 'additionalProperties', 'items', 'minimum', 'minLength', 'pattern', 'format'}
    if set(schema) - supported:
        raise ContractError('Unsupported schema keyword')
    kinds = {'object': dict, 'array': list, 'string': str, 'integer': int, 'boolean': bool, 'null': type(None)}
    names = schema.get('type', list(kinds))
    if isinstance(names, str):
        names = [names]
    if type(value) not in [kinds[n] for n in names]:
        raise ContractError('Schema type mismatch')
    if 'const' in schema and (type(value) is not type(schema['const']) or value != schema['const']):
        raise ContractError('Unknown schema version or constant')
    if 'enum' in schema and value not in schema['enum']:
        raise ContractError('Unknown enum value')
    if isinstance(value, dict):
        props = schema.get('properties', {})
        if set(schema.get('required', [])) - set(value):
            raise ContractError('Missing required field')
        if schema.get('additionalProperties') is False and set(value) - set(props):
            raise ContractError('Unknown field')
        for key in props.keys() & value.keys():
            _validate(props[key], value[key])
    if isinstance(value, list) and 'items' in schema:
        for item in value:
            _validate(schema['items'], item)
    if type(value) is int and value < schema.get('minimum', value):
        raise ContractError('Integer below minimum')
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or ('pattern' in schema and not re.fullmatch(schema['pattern'], value)):
            raise ContractError('Invalid string field')
        if schema.get('format') == 'date-time':
            timestamp(value)


def validate(kind, value):
    if kind not in ('record', 'designation', 'event', 'relation', 'manifest', 'receipt'):
        raise ContractError('Unknown schema')
    schema = strict_json((Path(__file__).parent / 'schemas' / (kind + '.json')).read_bytes())
    canonical(value)
    _validate(schema, value)
    if kind in ('record', 'relation', 'designation') and value['hash'] != record_hash(value):
        raise ContractError('Record hash mismatch')
    if kind == 'record':
        data = value['data']
        required = {'candidate': ('extractor', 'extractor_version', 'model'),
                    'decision': ('scope', 'supersedes', 'superseded_by'),
                    'practice': ('version', 'in_force_from', 'in_force_to', 'agents_md_ref')}.get(value['type'], ())
        if any(k not in data for k in required):
            raise ContractError('Missing type-specific record fields')
        if value['type'] == 'practice':
            timestamp(data['in_force_from'])
            if data['in_force_to'] is not None:
                timestamp(data['in_force_to'])
