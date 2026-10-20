"""Owner correction ledger projection; no authority is inferred from words.

Inputs come from the verified statement journal and separate attributed Shelf
annotation records. The owner-control adapter must verify an annotation's exact
payload digest against its completed owner-action receipt on EVERY projection.
This module cannot issue that receipt, adopt a lesson, or grant a capability.
No topic-string matching or model assertion confirms a repeated correction.
Missing capture evidence remains unknown; fewer captures never imply success.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re

from core.minimoi_actions.context import ActionContext, _aware, _identifier
from core.minimoi_actions.receipts import _uuid
from core.memory_shelf.render import scrub_text
from core.workshop_journal.errors import InvalidInput
from .statement_journal import _encode, _decode, _hash, _date, _scope, validate_payload

_ULID = re.compile(r'[0-9A-HJKMNP-TV-Z]{26}')
_FIELDS = frozenset('schema annotation_id statement_id span_key principal_id environment scope_id '
    'kind topic repeat_of confirmed_repeat_of actor_id recorded_at owner_receipt_id base_revision'.split())


@dataclass(frozen=True, repr=False)
class Annotation:
    """Persisted data, not authentication. Sequence is assigned by the writer."""
    sequence: int
    encoded: bytes

    def __post_init__(self):
        try:
            if type(self.sequence) is not int or self.sequence < 1 or type(self.encoded) is not bytes or len(self.encoded) > 8192:
                raise ValueError()
            p = _decode(self.encoded)
            if (type(p) is not dict or set(p) != _FIELDS or p['schema'] != 'mc-statement-annotation/1'
                    or type(p['annotation_id']) is not str or not _ULID.fullmatch(p['annotation_id'])
                    or not _uuid(p['statement_id']) or type(p['span_key']) is not str
                    or not re.fullmatch('[0-9a-f]{64}',p['span_key']) or not _scope(p['scope_id'])
                    or type(p['base_revision']) is not str or not re.fullmatch('[0-9a-f]{64}',p['base_revision'])
                    or not _identifier(p['principal_id']) or p['environment'] not in ('dev','prod')
                    or p['kind'] not in ('direction','correction','decision')
                    or type(p['topic']) is not str or len(p['topic']) > 160
                    or scrub_text(p['topic']) != (p['topic'],False) or not _identifier(p['actor_id'])
                    or (p['owner_receipt_id'] is not None and not _uuid(p['owner_receipt_id']))):
                raise ValueError()
            for key in ('repeat_of','confirmed_repeat_of'):
                if (type(p[key]) is not list or len(p[key]) > 32 or not all(_uuid(x) for x in p[key])
                        or len(set(p[key])) != len(p[key])):
                    raise ValueError()
            if (not set(p['confirmed_repeat_of']) <= set(p['repeat_of'])
                    or (p['owner_receipt_id'] is None and p['confirmed_repeat_of'])):
                raise ValueError()
            _date(p['recorded_at'])
            object.__setattr__(self,'encoded',_encode(p))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise InvalidInput('statement_annotation_invalid') from None

    @property
    def data(self):
        return json.loads(self.encoded)

    @property
    def digest(self):
        # Receipt ID is the result reference, not part of the approved payload.
        return _hash(_encode({k:v for k,v in self.data.items() if k != 'owner_receipt_id'}))


@dataclass(frozen=True)
class CaptureCoverage:
    """Trusted capture/audit counters for the exact requested window.

    An audit can establish misses. No sample/audit means unknown, not zero.
    eligible/captured/excluded refer to source turns, not extracted statements.
    """
    start: datetime
    end: datetime
    status: str = 'unavailable'
    eligible_turns: int | None = None
    captured_turns: int | None = None
    excluded_turns: int | None = None
    missed_statements: int | None = None
    audited_turns: int | None = None
    task_volume: int | None = None
    usefulness_record_id: str | None = None

    def __post_init__(self):
        if (not _aware(self.start) or not _aware(self.end) or self.start >= self.end
                or self.status not in ('complete','partial','unavailable')):
            raise InvalidInput('correction_coverage_invalid')
        for key in ('eligible_turns','captured_turns','excluded_turns','missed_statements','audited_turns','task_volume'):
            value = getattr(self,key)
            if value is not None and (type(value) is not int or not 0 <= value <= 100000000):
                raise InvalidInput('correction_coverage_invalid')
        if (self.eligible_turns is not None and self.captured_turns is not None
                and self.captured_turns > self.eligible_turns):
            raise InvalidInput('correction_coverage_invalid')
        if self.status == 'complete' and (self.eligible_turns is None or self.captured_turns != self.eligible_turns):
            raise InvalidInput('correction_coverage_invalid')
        if self.missed_statements is not None and self.audited_turns is None:
            raise InvalidInput('correction_coverage_invalid')
        if self.usefulness_record_id is not None and (type(self.usefulness_record_id) is not str
                or not _ULID.fullmatch(self.usefulness_record_id)):
            raise InvalidInput('correction_coverage_invalid')


def project_corrections(*, context, rows, annotations, start, end, now, coverage,
                        can_read_statement, verify_owner_annotation):
    """Return authorized site data; never suitable for a Telegram text dump.

    Callbacks are trusted adapters, not supplied by a model. False/unavailable
    verification cannot become confirmation. A malformed journal snapshot is
    refused, never interpreted as an empty ledger. Revoked sources are excluded
    and coverage is downgraded. The caller resolves source IDs to accessible
    links; this code never trusts a model path or URL.
    """
    if (type(context) is not ActionContext or context.privacy_mode not in ('recorded','on_record')
            or not _aware(now) or context.has_expired(now) or not _aware(start) or not _aware(end)
            or not start < end <= now or type(coverage) is not CaptureCoverage
            or (coverage.start,coverage.end) != (start,end)
            or type(rows) not in (list,tuple) or len(rows) > 50000
            or type(annotations) not in (list,tuple) or len(annotations) > 50000):
        raise InvalidInput('correction_projection_invalid')
    sources, effective, denied, warnings = {}, {}, 0, []
    spans, statement_ids, previous = set(), set(), 0
    for row in rows:
        if type(row) is not dict or type(row.get('sequence')) is not int or row['sequence'] <= previous:
            raise InvalidInput('correction_snapshot_invalid')
        previous = row['sequence']
        p = validate_payload(_encode(row.get('payload')))
        if (p['statement_id'] in statement_ids or p['span_key'] in spans
                or p['principal_id'] != context.principal_id or p['environment'] != context.environment):
            raise InvalidInput('correction_snapshot_scope_or_duplicate')
        spans.add(p['span_key']); statement_ids.add(p['statement_id'])
        if _date(p['captured_at']) > now or _date(p['occurred_at']) > now:
            raise InvalidInput('correction_snapshot_future')
        # Explicit True only. Unknown permission never leaks a quote/count.
        if can_read_statement(context,p) is not True:
            denied += 1
            continue
        sources[p['statement_id']] = p
        effective[p['statement_id']] = dict(kind=p['kind'],topic=p['topic'],
            repeat_of=list(p['repeat_of']),confirmed_repeat_of=[],annotation_id=None)
    ordered = sorted(annotations,key=lambda a:a.sequence if type(a) is Annotation else 0)
    seen_ids, seen_seq, proposals = set(), set(), []
    for annotation in ordered:
        if type(annotation) is not Annotation or annotation.sequence in seen_seq:
            raise InvalidInput('correction_annotations_invalid')
        p = annotation.data
        if p['annotation_id'] in seen_ids:
            raise InvalidInput('correction_annotations_invalid')
        seen_seq.add(annotation.sequence); seen_ids.add(p['annotation_id'])
        target = sources.get(p['statement_id'])
        if target is None:
            warnings.append('annotation_source_unavailable')
            continue
        if ((p['principal_id'],p['environment'],p['scope_id'],p['span_key']) !=
                (target['principal_id'],target['environment'],target['scope_id'],target['span_key'])
                or not _date(target['captured_at']) <= _date(p['recorded_at']) <= now):
            warnings.append('annotation_binding_invalid')
            continue
        if p['owner_receipt_id'] is None:
            proposed_links = []
            for sid in p['repeat_of']:
                prior = sources.get(sid)
                if (prior is None or sid == p['statement_id'] or prior['scope_id'] != target['scope_id']
                        or _date(prior['occurred_at']) > _date(target['occurred_at'])):
                    warnings.append('proposed_repeat_link_invalid')
                else:
                    proposed_links.append(sid)
            proposals.append(dict(annotation_id=p['annotation_id'],statement_id=p['statement_id'],
                kind=p['kind'],topic=p['topic'],repeat_of=proposed_links,standing='proposed'))
            continue
        if verify_owner_annotation(context,p,annotation.digest) is not True:
            warnings.append('annotation_owner_receipt_unverified')
            continue
        effective[p['statement_id']] = {k:p[k] for k in (
            'kind','topic','repeat_of','confirmed_repeat_of','annotation_id')}
    # Validate links against final labels and authorized source metadata. An
    # independently relabelled target can invalidate a previously valid link.
    graph = {}
    for sid, labels in effective.items():
        p = sources[sid]
        valid = []
        for target_id in labels['repeat_of']:
            target = sources.get(target_id)
            if (target_id == sid or target is None or target['scope_id'] != p['scope_id']
                    or _date(target['occurred_at']) > _date(p['occurred_at'])
                    or labels['kind'] != 'correction' or effective[target_id]['kind'] != 'correction'):
                warnings.append('repeat_link_invalid')
                continue
            valid.append(target_id)
        graph[sid] = valid
    # Iterative search bounds both depth and work; a cycle or excessive ancestry
    # is withheld instead of converting uncertain links into measured progress.
    traversed = 0
    for sid in graph:
        stack = [(t,1) for t in graph[sid]]
        visited, invalid = set(), False
        while stack:
            traversed += 1
            if traversed > 2000000:
                raise InvalidInput('correction_graph_work_limit')
            target, depth = stack.pop()
            if target == sid or depth > 64 or len(visited) > 4096:
                invalid = True
                break
            if target in visited:
                continue
            visited.add(target)
            stack.extend((t,depth+1) for t in graph[target])
        if invalid:
            warnings.append('repeat_cycle_or_limit')
            # Keep the original graph during all searches; remove after scan.
            labels = effective[sid]
            labels['invalid_graph'] = True
    entries, confirmed, provisional = [], 0, 0
    for sid,p in sources.items():
        labels = effective[sid]
        if labels['kind'] != 'correction' or not start <= _date(p['occurred_at']) < end:
            continue
        links = [] if labels.get('invalid_graph') else graph[sid]
        confirmed_links = [t for t in links if t in labels['confirmed_repeat_of']]
        provisional_links = [t for t in links if t not in labels['confirmed_repeat_of']]
        confirmed += bool(confirmed_links)
        # Headline statuses are disjoint; all pending edges remain visible.
        provisional += bool(provisional_links) and not bool(confirmed_links)
        entries.append(dict(statement_id=sid,quote=p['quote'],occurred_at=p['occurred_at'],
            original_kind=p['kind'],original_topic=p['topic'],kind=labels['kind'],topic=labels['topic'],
            annotation_id=labels['annotation_id'],confirmed_repeat_of=confirmed_links,
            provisional_repeat_of=provisional_links,fidelity=p['fidelity'],redaction_count=p['redaction_count'],
            source=dict(record_id=p['source_record_id'],conversation_id=p['conversation_id'],
                turn_id=p['turn_id'],edition_hash=p['source_edition_hash'],start=p['start'],end=p['end'],
                range_hash=p['range_hash'])))
    entries.sort(key=lambda x:(_date(x['occurred_at']),x['statement_id']))
    n = len(entries)
    state = coverage.status
    if (denied or warnings or (coverage.missed_statements or 0) > 0) and state == 'complete':
        state = 'partial'
    return dict(schema='mc-correction-ledger/1',as_of=now.astimezone(timezone.utc).isoformat(),
        window=dict(start=start.isoformat(),end=end.isoformat()),corrections=n,
        confirmed_repeats=confirmed,provisional_repeats=provisional,
        confirmed_repeat_fraction=confirmed/n if n else None,
        fraction_status='available' if n else 'no_data',entries=entries,
        proposed_annotations=proposals,warning_codes=sorted(set(warnings)),
        coverage=dict(status=state,eligible_turns=coverage.eligible_turns,captured_turns=coverage.captured_turns,
            excluded_turns=coverage.excluded_turns,missed_statements=coverage.missed_statements,
            audited_turns=coverage.audited_turns,task_volume=coverage.task_volume,
            usefulness_record_id=coverage.usefulness_record_id,
            source_access='partial' if denied else 'complete'))
