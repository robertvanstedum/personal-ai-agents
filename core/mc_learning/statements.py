"""Source-faithful owner statement preparation (harness §8.1).

No interpretation becomes approval. No text is read until trusted metadata
passes owner, conversation, environment, capture/privacy and edition checks.
The injected reader MUST revalidate access/edition when reading (including
revocation since describe); it accepts a descriptor, never a model path/URL.
Use only already committed scrubbed turn editions. This module does not capture
new sources, scan archives, write files or call a model.

The returned immutable payload is ready for the statement journal. UUID/dates
come from the server's durable prepare intent; the journal deduplicates source
spans, assigns sequence/checksum and reconciles append-before-receipt crashes.
Classification and repeat links remain proposed until separate review records.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re

from core.memory_shelf.render import scrub_text
from core.minimoi_actions.context import ActionContext, ActionContractError, _aware, _identifier
from core.minimoi_actions.receipts import _uuid
from core.minimoi_actions.requests import _object, _constant

MAX_TURN_BYTES = 256*1024
MAX_QUOTE_BYTES = 16*1024
_HASH = re.compile('[0-9a-f]{64}')
_REQUIRED = {'turn_id','source_edition_hash','start','end','kind','topic','repeat_of'}
_OPTIONAL = {'quote','work_id'}


def _hash(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class StatementRequest:
    """Untrusted classification and exact source-span proposal, never authority."""
    raw: bytes

    def __post_init__(self):
        try:
            if type(self.raw) is not bytes or len(self.raw) > 32*1024:
                raise ValueError()
            doc = json.loads(self.raw.decode('utf-8'), object_pairs_hook=_object, parse_constant=_constant)
            if (type(doc) is not dict or not _REQUIRED <= set(doc) or set(doc)-_REQUIRED-_OPTIONAL
                    or not _identifier(doc['turn_id']) or type(doc['source_edition_hash']) is not str
                    or not _HASH.fullmatch(doc['source_edition_hash'])
                    or type(doc['start']) is not int or type(doc['end']) is not int
                    or not 0 <= doc['start'] < doc['end'] <= MAX_TURN_BYTES
                    or doc['end']-doc['start'] > MAX_QUOTE_BYTES
                    or type(doc['kind']) is not str or doc['kind'] not in ('correction','direction','decision')
                    or type(doc['topic']) is not str or len(doc['topic']) > 160
                    or type(doc['repeat_of']) is not list or len(doc['repeat_of']) > 32
                    or not all(_uuid(x) for x in doc['repeat_of'])
                    or len(set(doc['repeat_of'])) != len(doc['repeat_of'])
                    or (doc.get('work_id') is not None and not _uuid(doc['work_id']))):
                raise ValueError()
            if 'quote' in doc and (type(doc['quote']) is not str or len(doc['quote'].encode('utf-8')) > MAX_QUOTE_BYTES):
                raise ValueError()
            clean_topic, changed = scrub_text(doc['topic'])
            if changed or clean_topic != doc['topic']:
                raise ValueError()
            encoded = json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            object.__setattr__(self, 'raw', encoded)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ActionContractError('statement_request_invalid') from None

    @property
    def data(self):
        return json.loads(self.raw)


@dataclass(frozen=True, slots=True, repr=False)
class SourceDescriptor:
    """Trusted source adapter metadata, NOT something the model supplies.

    edition_hash names the stored scrubbed owner-turn bytes, not an original
    unsanitized archive or the hash of an entire multi-turn conversation file.
    source_record_id preserves the source's native ID (a Shelf ID stays a ULID).
    Redaction ranges use offsets in those same scrubbed bytes decoded as UTF-8.
    """
    principal_id: str
    environment: str
    conversation_id: str
    turn_id: str
    source_record_id: str
    scope_id: str
    speaker_id: str
    speaker_role: str
    edition_hash: str
    occurred_at: datetime
    committed: bool
    capture_approved: bool
    privacy_mode: str
    redaction_spans: tuple[tuple[int, int, int], ...] = ()

    def __post_init__(self):
        if (not all(_identifier(x) for x in (self.principal_id,self.conversation_id,self.turn_id,
                    self.source_record_id,self.scope_id,self.speaker_id,self.speaker_role,self.privacy_mode))
                or self.environment not in ('dev','prod') or not _aware(self.occurred_at)
                or type(self.edition_hash) is not str or not _HASH.fullmatch(self.edition_hash)
                or type(self.committed) is not bool or type(self.capture_approved) is not bool
                or type(self.redaction_spans) is not tuple or len(self.redaction_spans) > 1024):
            raise ActionContractError('statement_source_invalid')
        last_end = 0
        for span in self.redaction_spans:
            if (type(span) is not tuple or len(span) != 3 or any(type(x) is not int for x in span)
                    or not last_end <= span[0] < span[1] <= MAX_TURN_BYTES or not 1 <= span[2] <= 1024):
                raise ActionContractError('statement_source_invalid')
            last_end = span[1]


@dataclass(frozen=True, slots=True, repr=False)
class RepeatTarget:
    statement_id: str
    principal_id: str
    environment: str
    scope_id: str
    occurred_at: datetime
    ancestor_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class PreparedStatement:
    encoded: bytes

    @property
    def data(self):
        return json.loads(self.encoded)


def prepare_statement(*, context, proposal, statement_id, now, model_id,
                      describe_source, read_source, lookup_repeat):
    """Prepare only; exact equality includes Unicode/CRLF/whitespace.

    Off-record calls and foreign source metadata are refused before read_source.
    The reader returns at most MAX_TURN_BYTES of one existing edition. Its source
    policy check must be fresh. Lookup returns only already committed, visible
    statement metadata and a validated ancestry, never arbitrary model claims.
    """
    if (type(context) is not ActionContext or type(proposal) is not StatementRequest
            or not _uuid(statement_id) or not _aware(now) or not _identifier(model_id)):
        raise ActionContractError('statement_admission_invalid')
    if context.privacy_mode not in ('recorded','on_record') or context.has_expired(now):
        raise ActionContractError('statement_capture_refused')
    request = proposal.data
    if request.get('work_id') != context.work_id:
        raise ActionContractError('statement_work_mismatch')
    source = describe_source(context, request['turn_id'], request['source_edition_hash'])
    if type(source) is not SourceDescriptor:
        raise ActionContractError('statement_source_unavailable')
    if (source.principal_id != context.principal_id or source.environment != context.environment
            or source.conversation_id != context.conversation_id or source.turn_id != request['turn_id']
            or source.speaker_id != context.principal_id or source.speaker_role != 'owner'
            or source.privacy_mode not in ('recorded','on_record') or not source.committed
            or not source.capture_approved):
        raise ActionContractError('statement_source_refused')
    if source.edition_hash != request['source_edition_hash'] or source.occurred_at > now:
        raise ActionContractError('statement_source_changed')
    for target_id in request['repeat_of']:
        target = lookup_repeat(context, target_id)
        if (type(target) is not RepeatTarget or target.statement_id != target_id
                or target.principal_id != context.principal_id or target.environment != context.environment
                or target.scope_id != source.scope_id or not _aware(target.occurred_at)
                or target.occurred_at > source.occurred_at or target.statement_id == statement_id
                or type(target.ancestor_ids) is not tuple or len(target.ancestor_ids) > 64
                or not all(_uuid(x) for x in target.ancestor_ids) or statement_id in target.ancestor_ids):
            raise ActionContractError('statement_repeat_refused')
    raw = read_source(context, source, MAX_TURN_BYTES)
    if type(raw) is not bytes or len(raw) > MAX_TURN_BYTES:
        raise ActionContractError('statement_source_unavailable')
    if _hash(raw) != source.edition_hash:
        raise ActionContractError('statement_source_changed')
    try:
        text = raw.decode('utf-8')
        # Changing a source here would invalidate its edition/offset provenance.
        # Refuse it and let the established capture path produce a scrubbed one.
        scrubbed, changed = scrub_text(text)
        if changed or scrubbed != text:
            raise ActionContractError('statement_source_not_scrubbed')
        if request['end'] > len(text) or any(end > len(text) for _, end, _ in source.redaction_spans):
            raise ActionContractError('statement_range_invalid')
        quote = text[request['start']:request['end']]
        if len(quote.encode('utf-8')) > MAX_QUOTE_BYTES:
            raise ActionContractError('statement_quote_too_large')
    except UnicodeError:
        raise ActionContractError('statement_source_unavailable') from None
    if 'quote' in request and request['quote'] != quote:
        raise ActionContractError('statement_quote_mismatch')
    redactions = sum(count for start, end, count in source.redaction_spans
                     if request['start'] < end and start < request['end'])
    identity = [context.principal_id, context.environment, context.conversation_id,
                source.turn_id, source.edition_hash, request['start'], request['end']]
    span_key = _hash(json.dumps(identity, ensure_ascii=True, separators=(',', ':')).encode('ascii'))
    payload = dict(schema='mc-owner-statement/1', statement_id=statement_id, span_key=span_key,
        principal_id=context.principal_id, environment=context.environment, conversation_id=context.conversation_id,
        turn_id=source.turn_id, source_record_id=source.source_record_id, source_edition_hash=source.edition_hash,
        scope_id=source.scope_id, speaker_id=source.speaker_id, speaker_role=source.speaker_role,
        start=request['start'], end=request['end'], quote=quote, range_hash=_hash(quote.encode('utf-8')),
        fidelity='redacted_source' if redactions else 'exact_scrubbed_source', redaction_count=redactions,
        kind=request['kind'], topic=request['topic'], repeat_of=request['repeat_of'], repeat_standing='proposed',
        proposing_actor=context.actor_id, proposing_model=model_id, work_id=context.work_id,
        occurred_at=source.occurred_at.astimezone(timezone.utc).isoformat(),
        captured_at=now.astimezone(timezone.utc).isoformat(), privacy_mode='on_record')
    return PreparedStatement(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                       separators=(',', ':')).encode('utf-8'))
