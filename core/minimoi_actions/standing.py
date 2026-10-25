"""Standing ingress contracts (harness §14.1); producer data grants no authority.

Policy values are installed by the operator from approved setup. The producer
may name an opaque payload and destination binding, never a filesystem path,
URL to fetch, owner, floor or conversation ID to impersonate.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import UUID

from .context import ActionContractError, _identifier
from .requests import MAX_REQUEST_BYTES, _object, _constant, _bounded_tree

_SHA = re.compile(r'[0-9a-f]{64}')
_OPAQUE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,150}')
_FIELDS = {'schema', 'event_id', 'cycle_key', 'policy_hash', 'environment', 'kind',
           'occurred_at', 'source_refs', 'finding_key', 'payload_ref', 'payload_digest',
           'destination_binding'}


@dataclass(frozen=True, slots=True, repr=False)
class Destination:
    binding_id: str
    floor_key: str

    def __post_init__(self):
        if not _identifier(self.binding_id) or not _identifier(self.floor_key):
            raise ActionContractError('standing_destination_invalid')


@dataclass(frozen=True, slots=True, repr=False)
class ProducerPolicy:
    producer_id: str
    principal_id: str
    environment: str
    token_sha256: str
    policy_hashes: tuple[str, ...]
    destinations: tuple[Destination, ...]

    def __post_init__(self):
        if not all(_identifier(x) for x in (self.producer_id, self.principal_id)):
            raise ActionContractError('standing_policy_invalid')
        if type(self.environment) is not str or self.environment not in {'dev', 'prod'}:
            raise ActionContractError('standing_policy_invalid')
        if type(self.token_sha256) is not str or not _SHA.fullmatch(self.token_sha256):
            raise ActionContractError('standing_policy_invalid')
        if (type(self.policy_hashes) is not tuple or not self.policy_hashes
                or not all(type(x) is str and _SHA.fullmatch(x) for x in self.policy_hashes)):
            raise ActionContractError('standing_policy_invalid')
        if (type(self.destinations) is not tuple
                or not all(type(x) is Destination for x in self.destinations)
                or len({x.binding_id for x in self.destinations}) != len(self.destinations)):
            raise ActionContractError('standing_policy_invalid')


@dataclass(frozen=True, slots=True, repr=False)
class StandingEvent:
    """Validated immutable wire bytes; properties return copies."""
    raw: bytes

    def __post_init__(self):
        if type(self.raw) is not bytes or len(self.raw) > MAX_REQUEST_BYTES:
            raise ActionContractError('standing_event_size_invalid')
        try:
            data = json.loads(self.raw.decode('utf-8'), object_pairs_hook=_object,
                              parse_constant=_constant)
            _bounded_tree(data)
            if type(data) is not dict or set(data) != _FIELDS or data['schema'] != 'mc-standing-event/1':
                raise ActionContractError('standing_event_fields_invalid')
            if type(data['event_id']) is not str or str(UUID(data['event_id'])) != data['event_id']:
                raise ActionContractError('standing_event_id_invalid')
            for key in ('cycle_key', 'payload_ref'):
                if type(data[key]) is not str or not _OPAQUE.fullmatch(data[key]):
                    raise ActionContractError('standing_reference_invalid')
            for key in ('policy_hash', 'payload_digest'):
                if type(data[key]) is not str or not _SHA.fullmatch(data[key]):
                    raise ActionContractError('standing_digest_invalid')
            if data['environment'] not in ('dev', 'prod') or data['kind'] not in ('brief', 'finding', 'recovery'):
                raise ActionContractError('standing_event_kind_invalid')
            when = datetime.fromisoformat(data['occurred_at'].replace('Z', '+00:00'))
            if when.utcoffset() != timezone.utc.utcoffset(when):
                raise ActionContractError('standing_timestamp_invalid')
            refs = data['source_refs']
            if type(refs) is not list or len(refs) > 64:
                raise ActionContractError('standing_sources_invalid')
            for ref in refs:
                if (type(ref) is not dict or set(ref) != {'id', 'digest'}
                        or not _identifier(ref['id']) or type(ref['digest']) is not str
                        or not _SHA.fullmatch(ref['digest'])):
                    raise ActionContractError('standing_sources_invalid')
            for key in ('finding_key', 'destination_binding'):
                if data[key] is not None and not _identifier(data[key]):
                    raise ActionContractError('standing_reference_invalid')
            if data['kind'] == 'brief' and data['destination_binding'] is None:
                raise ActionContractError('standing_destination_invalid')
            canonical = json.dumps(data, sort_keys=True, separators=(',', ':'),
                                   ensure_ascii=False, allow_nan=False).encode('utf-8')
            if len(canonical) > MAX_REQUEST_BYTES:
                raise ActionContractError('standing_event_size_invalid')
            object.__setattr__(self, 'raw', canonical)
        except ActionContractError:
            raise
        except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
            raise ActionContractError('standing_event_invalid') from None

    @property
    def data(self) -> dict:
        return json.loads(self.raw)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()

    def destination(self, policy: ProducerPolicy) -> Destination | None:
        data = self.data
        if data['environment'] != policy.environment or data['policy_hash'] not in policy.policy_hashes:
            raise ActionContractError('standing_policy_mismatch')
        binding = data['destination_binding']
        found = next((d for d in policy.destinations if d.binding_id == binding), None)
        if binding is not None and found is None:
            raise ActionContractError('standing_destination_unbound')
        return found


def ack(event_id: str, state: str, code: str, record_id=None) -> dict:
    return dict(schema='mc-standing-ack/1', event_id=event_id, state=state,
                record_id=str(record_id) if record_id is not None else None,
                work_id=None, code=code)


def brief_text(payload: bytes, expected_digest: str) -> str:
    """The resolver returns authorized UTF-8 content, never an arbitrary path.

    The platform-note limit is 4000 characters; full evidence stays in the
    authorized artifact. Oversize content is refused, never silently clipped.
    """
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_REQUEST_BYTES:
        raise ActionContractError('standing_payload_size_invalid')
    if hashlib.sha256(payload).hexdigest() != expected_digest:
        raise ActionContractError('standing_payload_digest_mismatch')
    try:
        text = payload.decode('utf-8')
    except UnicodeError:
        raise ActionContractError('standing_payload_invalid') from None
    if not text.strip() or len(text) > 4000 or '\x00' in text:
        raise ActionContractError('standing_payload_invalid')
    return text
