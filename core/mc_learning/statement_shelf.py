"""Normalize an already captured statement into the existing Shelf/Vault.

The journal remains the source, and the existing Shelf lock, bundle, immutable
edition, capture ledger and source index own publication. A durable mapping
allocates a real Shelf ULID once; a statement/action UUID is only correlation.
No imported word can designate, adopt or approve anything. This is not a new
capture-source approval or a live job. The caller supplies fresh source-access
verification and invokes this only for its approved memory root.
"""
from datetime import timezone
import os
import re

from core.memory_shelf import codes, render, ulid
from core.memory_shelf.bundle import from_parsed
from core.memory_shelf.sessions import Parsed, Turn
from core.memory_shelf.shelf import Shelf, Result
from core.minimoi_actions.context import ActionContext, _aware
from core.minimoi_actions.receipts import _uuid
from core.workshop_journal import fsutil as fs
from core.workshop_journal.errors import Corrupt, InvalidInput, Missing
from core.vault_t1.reader import Vault, VaultError
from .statement_journal import StatementJournal, _date, _encode, _decode, _hash


def _bundle(row, record_id):
    p = row['payload']
    source_hash = _hash(_encode(row))
    created = _date(p['captured_at']).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    source_id = _hash(_encode([p['principal_id'],p['environment'],p['scope_id'],p['statement_id']]))
    parsed = Parsed(provider='minimoi-owner-statement',chair='MC',source_id=source_id,
                    started=p['occurred_at'],designatable=False,normalizer=1)
    # Exact bounded bytes live in the standard framed turn. Native identity and
    # the surrounding source reference live in an inert reference, not a URL.
    parsed.turns = [Turn(1,'human',p['quote'],1,'authenticated-owner-source-span',
                         {'who':'owner','ts':_date(p['occurred_at']).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})]
    parsed.references = [dict(kind='owner-statement-source',statement_id=p['statement_id'],
        source_record_id=p['source_record_id'],conversation_id=p['conversation_id'],turn_id=p['turn_id'],
        principal_id=p['principal_id'],environment=p['environment'],speaker_id=p['speaker_id'],
        speaker_role=p['speaker_role'],source_edition_hash=p['source_edition_hash'],
        start=p['start'],end=p['end'],range_hash=p['range_hash'],fidelity=p['fidelity'],
        redaction_count=p['redaction_count'],original_kind=p['kind'],original_topic=p['topic'],
        proposed_repeat_of=p['repeat_of'],proposing_actor=p['proposing_actor'],proposing_model=p['proposing_model'],
        commit_sequence=row['sequence'],journal_entry_hash=row['entry_hash'])]
    bundle = from_parsed(parsed,source_hash,len(_encode(row)),key=f'minimoi-owner-statement:{source_id}',
        title='Owner statement',origin='normalizer:mc-owner-statements',created=created,
        kind='owner-statement',tags=['owner-statement'],ledger_key=p['span_key'])
    bundle.meta['id'] = record_id
    bundle.meta['scope'] = p['scope_id']
    bundle.meta['owner_statement_id'] = p['statement_id']
    bundle.meta['statement_span_key'] = p['span_key']
    # Existing rendering must not quietly alter an already scrubbed quotation.
    turns = render.parse_body(bundle.body)
    if len(turns) != 1 or turns[0].text != p['quote'] or bundle.designation is not None or bundle.meta['events']:
        raise Corrupt('statement_normalization_changed_source')
    return bundle


def _record_id(shelf, row):
    """Called under Shelf.lock. Only an inert ID/hash map, never owner text."""
    p = row['payload']
    expected = dict(schema='mc-statement-shelf-map/1',statement_id=p['statement_id'],
        span_key=p['span_key'],principal_id=p['principal_id'],environment=p['environment'],
        scope_id=p['scope_id'],source_hash=_hash(_encode(row)))
    fds = []
    try:
        fds.append(fs.open_root(str(shelf.root)))
        for part in ('_status','mc-owner-statements'):
            parent = fds[-1]
            fds.append(fs.open_subdir(parent,part,create=True))
            fs.fsync_dir(parent)
        folder = fds[-1]
        name = _hash(_encode([p['principal_id'],p['environment'],p['scope_id'],p['span_key']]))+'.json'
        try:
            fd = fs.open_file(folder,name,os.O_RDONLY)
        except Missing:
            record_id = ulid.new_ulid(int(_date(p['captured_at']).timestamp()*1000))
            fs.publish_new(folder,name,_encode(expected|{'record_id':record_id}))
            return record_id
        try:
            if os.fstat(fd).st_size > 4096:
                raise Corrupt('statement_shelf_map_invalid')
            raw = os.read(fd,4097)
            fs.check_same_file(folder,name,fd)
        finally:
            os.close(fd)
        try:
            saved = _decode(raw)
            if (type(saved) is not dict or set(saved) != set(expected)|{'record_id'}
                    or any(saved[k] != v for k,v in expected.items())
                    or type(saved['record_id']) is not str
                    or not re.fullmatch(r'[0-9A-HJKMNP-TV-Z]{26}',saved['record_id'])):
                raise ValueError()
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise Corrupt('statement_shelf_map_invalid') from None
        return saved['record_id']
    finally:
        for fd in reversed(fds):
            os.close(fd)


def normalize_statement(*, shelf, journal, context, statement_id, now, can_read_statement):
    """Publish/reconcile one verified journal statement, with no live defaults.

    Authorization is checked before reading this principal's journal and again
    against the selected source immediately before writing under the Shelf lock.
    The callback must check current source capture/privacy/scope policy, not just
    historical metadata. A failed/unknown Shelf result is not success; the
    caller retains its normalization retry pointer. No action receipt is closed
    here. Journal and normalized-record IDs remain separate.
    """
    if (type(shelf) is not Shelf or type(journal) is not StatementJournal
            or type(context) is not ActionContext or not _uuid(statement_id) or not _aware(now)
            or context.has_expired(now) or context.privacy_mode not in ('recorded','on_record')
            or (context.principal_id,context.environment) != (journal.principal,journal.environment)
            or shelf.root.absolute() != journal.root.absolute()):
        raise InvalidInput('statement_normalization_refused')
    rows = journal.snapshot()
    row = next((r for r in rows if r['payload']['statement_id'] == statement_id),None)
    if row is None:
        raise Missing('statement_not_captured')
    if can_read_statement(context,row['payload']) is not True:
        raise InvalidInput('statement_source_revoked')
    if shelf.headroom():
        return Result(codes.DISK_LOW,reason='disk_low')
    # Validate the existing shared lock path before entering its implementation.
    # This does not broaden the threat model to a hostile process under our UID.
    root = fs.open_root(str(shelf.root))
    try:
        for part in ('sessions-raw','sessions','notes','turns','snapshots','briefs','outbox','_status'):
            try:
                folder = fs.open_subdir(root,part)
            except Missing:
                continue
            os.close(folder)
        ledger = fs.open_subdir(root,'_ledger',create=True)
        try:
            lock = fs.open_file(ledger,'shelf.lock',os.O_RDWR|os.O_CREAT)
            os.close(lock)
            fs.fsync_dir(ledger)
        finally:
            os.close(ledger)
        fs.fsync_dir(root)
    finally:
        os.close(root)
    with shelf.lock(wait=5):
        if can_read_statement(context,row['payload']) is not True:
            raise InvalidInput('statement_source_revoked')
        if shelf.headroom():
            return Result(codes.DISK_LOW,reason='disk_low')
        bundle = _bundle(row,_record_id(shelf,row))
        entry = shelf.index().get(bundle.key)
        expected_stem = ulid.filename(bundle.title,bundle.meta['id'],'md')[:-3]
        if entry is not None and (type(entry) is not dict or entry.get('dir') != expected_stem
                                  or entry.get('id') != bundle.meta['id']):
            raise Corrupt('statement_shelf_identity_conflict')
        result = shelf.ingest(bundle)
        if result.outcome in codes.OK_OUTCOMES and result.record_id != bundle.meta['id']:
            raise Corrupt('statement_shelf_identity_conflict')
        if result.outcome in codes.OK_OUTCOMES:
            scope = row['payload']['scope_id']
            mandates = (scope[8:],) if scope.startswith('mandate:') else ()
            try:
                vault = Vault(str(shelf.root),mandates)
                rec, _, raw = vault.open(result.record_id)
                if (raw != bundle.edition or rec.meta.get('scope') != scope
                        or rec.meta.get('source') != bundle.key
                        or rec.meta.get('owner_statement_id') != statement_id):
                    raise Corrupt('statement_shelf_readback_mismatch')
            except VaultError:
                raise Corrupt('statement_shelf_readback_failed') from None
        return result
