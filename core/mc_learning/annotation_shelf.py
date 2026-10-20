"""Receipt-gated owner annotations in the existing Shelf, never in source lines.

The action service authenticates/authorizes before calling this adapter. It must
persist the immutable annotation payload (including ULID, time, base revision
and receipt UUID) in its DB intent before publication. The authorization callback
checks the exact digest AND current intent/grant/owner identity; Python data is
not authentication. This module does not expose a route or issue approval.

Private _status reservations make sequence assignment/retry durable under the
existing Shelf lock. They are recovery metadata; published annotations are
ordinary attributed Shelf records with immutable editions and empty events.
The ledger applies them only after a separate completed-receipt check. A file
write before the DB receipt commits is pending, not an approved label.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timezone
import fcntl
import os
import re
import time

from core.memory_shelf import codes, ulid
from core.memory_shelf.bundle import from_parsed
from core.memory_shelf.sessions import Parsed, Turn
from core.memory_shelf.shelf import Shelf
from core.minimoi_actions.context import ActionContext, _aware
from core.vault_t1.reader import Vault, VaultError, NotFound
from core.workshop_journal import fsutil as fs
from core.workshop_journal.errors import CommitUnknown, Corrupt, IdConflict, InvalidInput, LockBusy, Missing
from .corrections import Annotation
from .statement_journal import StatementJournal, _encode, _decode, _hash, _date, ZERO

MAX_RECORDS = 10000
MAX_TOTAL = 64 * 1024 * 1024
_ENTRY = re.compile(r'entry-[0-9]{8}\.json')


@dataclass(frozen=True)
class AnnotationSnapshot:
    annotations: tuple[Annotation, ...]
    pending_annotation_ids: tuple[str, ...]
    revisions: dict[str, str]


def _read(folder, name):
    fd = fs.open_file(folder,name,os.O_RDONLY)
    try:
        size = os.fstat(fd).st_size
        if size > 16384:
            raise Corrupt('annotation_metadata_limit')
        data = fs.read_all(fd)
        fs.check_same_file(folder,name,fd)
        if len(data) != size or os.fstat(fd).st_size != size:
            raise Corrupt('annotation_metadata_changed')
        return _decode(data)
    except (ValueError,TypeError,UnicodeError,RecursionError):
        raise Corrupt('annotation_metadata_invalid') from None
    finally:
        os.close(fd)


def _revision(source, entries):
    related = [e['entry_hash'] for e in entries if e['payload']['statement_id'] == source['payload']['statement_id']]
    return _hash(_encode([source['entry_hash'],related]))


def _record_map(vault):
    records = {}
    for rec in vault.records():
        if rec.id in records:
            raise Corrupt('annotation_shelf_id_ambiguous')
        records[rec.id] = rec
    return records


def _bundle(entry):
    p = entry['payload']
    created = _date(p['recorded_at']).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    identity = _hash(_encode([p['principal_id'],p['environment'],p['scope_id'],p['annotation_id']]))
    parsed = Parsed(provider='minimoi-owner-annotation',chair='MC',source_id=identity,
                    started=created,designatable=False,normalizer=1)
    # This is structured interpretation, never another verbatim owner quote.
    parsed.turns = [Turn(1,'assistant','Owner annotation; application requires a verified owner receipt.',
                         1,'owner-annotation',{'ts':created})]
    parsed.references = [dict(kind='owner-statement-annotation',sequence=entry['sequence'],
                               annotation=p,reservation_hash=entry['entry_hash'])]
    bundle = from_parsed(parsed,_hash(_encode(entry)),len(_encode(entry)),
        key='minimoi-owner-annotation:'+identity,title='Owner statement annotation',
        origin='owner-control:mc-statements',created=created,kind='owner-statement-annotation',
        tags=['owner-statement-annotation'],ledger_key=p['annotation_id'])
    bundle.meta.update(id=p['annotation_id'],scope=p['scope_id'],owner_statement_id=p['statement_id'],
                       annotation_partition=_hash(_encode([p['principal_id'],p['environment'],p['scope_id']])),
                       annotation_digest=Annotation(entry['sequence'],_encode(p)).digest)
    if bundle.meta['events'] or bundle.designation is not None:
        raise Corrupt('annotation_authority_event_refused')
    return bundle


class AnnotationShelf:
    def __init__(self, shelf, journal):
        if (type(shelf) is not Shelf or type(journal) is not StatementJournal
                or shelf.root.absolute() != journal.root.absolute()):
            raise InvalidInput('annotation_store_config_invalid')
        self.shelf,self.journal = shelf,journal

    def _context(self, context, now):
        if (type(context) is not ActionContext or not _aware(now) or context.has_expired(now)
                or context.privacy_mode not in ('recorded','on_record')
                or (context.principal_id,context.environment) != (self.journal.principal,self.journal.environment)):
            raise InvalidInput('annotation_context_refused')

    @contextmanager
    def _locked(self, *, write):
        """Read uses the same lock inode without creating or repairing any file."""
        fds = []
        try:
            root = fs.open_root(str(self.shelf.root)); fds.append(root)
            for name in ('sessions-raw','sessions','notes','turns','snapshots','briefs','outbox','_status'):
                try:
                    fd = fs.open_subdir(root,name)
                except Missing:
                    continue
                os.close(fd)
            try:
                ledger = fs.open_subdir(root,'_ledger',create=write); fds.append(ledger)
                lock = fs.open_file(ledger,'shelf.lock',(os.O_RDWR|os.O_CREAT) if write else os.O_RDONLY)
            except Missing:
                # No Shelf exists yet. A missing lock beside saved annotations is damage.
                if (self.shelf.root/'_status'/'mc-statement-annotations').exists():
                    raise Corrupt('annotation_lock_missing') from None
                yield None
                return
            fds.append(lock)
            if write:
                fs.fsync_dir(ledger); fs.fsync_dir(root)
                with self.shelf.lock(wait=5):
                    fs.check_same_file(ledger,'shelf.lock',lock)
                    yield self._folder(root,fds,create=True)
            else:
                end = time.monotonic()+2
                while True:
                    try:
                        fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= end:
                            raise LockBusy('annotation_lock_timeout') from None
                        time.sleep(.01)
                fs.check_same_file(ledger,'shelf.lock',lock)
                yield self._folder(root,fds,create=False)
        finally:
            for fd in reversed(fds):
                os.close(fd)

    def _folder(self, root, fds, *, create):
        parent = root
        for name in ('_status','mc-statement-annotations',self.journal.partition):
            try:
                fd = fs.open_subdir(parent,name,create=create)
            except Missing:
                return None
            fds.append(fd)
            if create:
                fs.fsync_dir(parent)
            parent = fd
        return parent

    def _scan(self, folder, *, initialize=False):
        if folder is None:
            return []
        names = sorted(n for n in os.listdir(folder) if not n.startswith('.tmp-'))
        if any(n != '.head.json' and not _ENTRY.fullmatch(n) for n in names):
            raise Corrupt('annotation_metadata_unexpected')
        files = [n for n in names if _ENTRY.fullmatch(n)]
        if len(files) > MAX_RECORDS:
            raise Corrupt('annotation_partition_limit')
        try:
            head = _read(folder,'.head.json')
        except Missing:
            if files:
                raise Corrupt('annotation_checkpoint_missing') from None
            if not initialize:
                return []
            head = dict(sequence=0,entry_hash=ZERO)
            fs.publish_new(folder,'.head.json',_encode(head))
        if (type(head) is not dict or set(head) != {'sequence','entry_hash'}
                or type(head['sequence']) is not int or not 0 <= head['sequence'] <= len(files)):
            raise Corrupt('annotation_checkpoint_invalid')
        entries,ids,receipts,total = [],set(),set(),0
        previous = ZERO
        for sequence,name in enumerate(files,1):
            if name != f'entry-{sequence:08}.json':
                raise Corrupt('annotation_sequence_gap')
            entry = _read(folder,name)
            try:
                if (type(entry) is not dict or set(entry) != {'sequence','previous','entry_hash','payload'}
                        or type(entry['sequence']) is not int or entry['sequence'] != sequence
                        or entry['previous'] != previous):
                    raise ValueError()
                annotation = Annotation(sequence,_encode(entry['payload'])); p = annotation.data
                if ((p['principal_id'],p['environment'],p['scope_id']) !=
                        (self.journal.principal,self.journal.environment,self.journal.scope)
                        or p['annotation_id'] in ids or p['owner_receipt_id'] in receipts
                        or p['owner_receipt_id'] is None or p['actor_id'] != self.journal.principal
                        or entry['entry_hash'] != _hash(_encode({k:v for k,v in entry.items() if k != 'entry_hash'}))):
                    raise ValueError()
            except (ValueError,TypeError,InvalidInput):
                raise Corrupt('annotation_reservation_invalid') from None
            ids.add(p['annotation_id']); receipts.add(p['owner_receipt_id'])
            total += len(_encode(entry))
            if total > MAX_TOTAL:
                raise Corrupt('annotation_partition_limit')
            previous = entry['entry_hash']; entries.append(entry)
        expected = entries[head['sequence']-1]['entry_hash'] if head['sequence'] else ZERO
        if head['entry_hash'] != expected:
            raise Corrupt('annotation_checkpoint_invalid')
        return entries

    def _published(self, entry, vault, records=None):
        bundle = _bundle(entry)
        records = records if records is not None else _record_map(vault)
        rec = records.get(bundle.meta['id'])
        if rec is None:
            return False
        try:
            edition = rec.current()
            if edition is None:
                raise Corrupt('annotation_shelf_readback_failed')
            raw = vault.edition_bytes(rec,edition)
        except VaultError as exc:
            raise Corrupt('annotation_shelf_readback_failed') from exc
        if (raw != bundle.edition or rec.meta.get('source') != bundle.key
                or rec.meta.get('scope') != bundle.meta['scope'] or rec.meta.get('events') != []
                or rec.meta.get('annotation_digest') != bundle.meta['annotation_digest']):
            raise Corrupt('annotation_shelf_readback_mismatch')
        return True

    def _vault(self):
        scope = self.journal.scope
        return Vault(str(self.shelf.root),(scope[8:],) if scope.startswith('mandate:') else ())

    def _check_known_records(self, vault, entries):
        known = {entry['payload']['annotation_id'] for entry in entries}
        # Losing the complete reservation folder must not silently reset the
        # sequence or turn already-published owner changes into an empty list.
        if any(rec.meta.get('annotation_partition') == self.journal.partition and rec.id not in known
               for rec in vault.records()):
            raise Corrupt('annotation_reservations_missing')

    def _sources(self, context, now, can_read_statement):
        sources = {}
        for row in self.journal.snapshot():
            p = row['payload']
            if _date(p['captured_at']) > now:
                raise Corrupt('annotation_source_future')
            if can_read_statement(context,p) is True:
                sources[p['statement_id']] = row
        return sources

    def snapshot(self, *, context, now, can_read_statement):
        self._context(context,now)
        with self._locked(write=False) as folder:
            sources = self._sources(context,now,can_read_statement)
            entries = self._scan(folder)
            vault = self._vault()
            self._check_known_records(vault,entries)
            records = _record_map(vault)
            related = {}
            for entry in entries:
                related.setdefault(entry['payload']['statement_id'],[]).append(entry['entry_hash'])
            published,pending = [],[]
            for entry in entries:
                p = entry['payload']
                if p['statement_id'] not in sources:
                    continue
                if self._published(entry,vault,records):
                    published.append(Annotation(entry['sequence'],_encode(p)))
                else:
                    pending.append(p['annotation_id'])
            return AnnotationSnapshot(tuple(published),tuple(pending),
                {sid:_hash(_encode([source['entry_hash'],related.get(sid,[])])) for sid,source in sources.items()})

    def publish(self, payload, *, context, now, can_read_statement, authorize_owner_annotation):
        """Publish/reconcile exact DB-intent bytes. Success is NOT receipt completion.

        authorize_owner_annotation(context, payload, digest) must check a current
        authenticated owner request and the exact dispatched action intent. A
        proposed lesson/annotation or quoted approval cannot satisfy it. Retry
        identical payload/receipt/ULID; changing any byte is a conflict.
        """
        self._context(context,now)
        annotation = Annotation(1,payload); p = annotation.data
        if (p['owner_receipt_id'] is None or p['actor_id'] != context.principal_id
                or (p['principal_id'],p['environment'],p['scope_id']) !=
                   (self.journal.principal,self.journal.environment,self.journal.scope)
                or _date(p['recorded_at']) > now):
            raise InvalidInput('annotation_owner_binding_refused')
        if authorize_owner_annotation(context,p,annotation.digest) is not True:
            raise InvalidInput('annotation_owner_action_refused')
        if self.shelf.headroom():
            raise InvalidInput('disk_low')
        with self._locked(write=True) as folder:
            if authorize_owner_annotation(context,p,annotation.digest) is not True:
                raise InvalidInput('annotation_owner_action_refused')
            sources = self._sources(context,now,can_read_statement)
            source = sources.get(p['statement_id'])
            if (source is None or source['payload']['span_key'] != p['span_key']
                    or _date(p['recorded_at']) < _date(source['payload']['captured_at'])):
                raise InvalidInput('annotation_source_refused')
            entries = self._scan(folder,initialize=True)
            self._check_known_records(self._vault(),entries)
            self._links(p,sources,entries)
            existing = next((e for e in entries if e['payload']['annotation_id'] == p['annotation_id']
                             or e['payload']['owner_receipt_id'] == p['owner_receipt_id']),None)
            if existing is not None:
                if existing['payload'] != p:
                    raise IdConflict('annotation_intent_conflict')
                entry = existing
            else:
                if p['base_revision'] != _revision(source,entries):
                    raise IdConflict('annotation_revision_changed')
                if len(entries) >= MAX_RECORDS:
                    raise InvalidInput('annotation_partition_limit')
                entry = dict(sequence=len(entries)+1,previous=entries[-1]['entry_hash'] if entries else ZERO,payload=p)
                entry['entry_hash'] = _hash(_encode(entry))
                if sum(len(_encode(e)) for e in entries)+len(_encode(entry)) > MAX_TOTAL:
                    raise InvalidInput('annotation_partition_limit')
            bundle = _bundle(entry)
            try:
                prior_record = self._vault().find(p['annotation_id'])
            except NotFound:
                prior_record = None
            if prior_record is not None and prior_record.meta.get('source') != bundle.key:
                raise IdConflict('annotation_shelf_id_in_use')
            if existing is None:
                try:
                    fs.publish_new(folder,f"entry-{entry['sequence']:08}.json",_encode(entry))
                except OSError:
                    raise CommitUnknown('annotation_reservation_unknown') from None
                entries.append(entry)
            if self.shelf.headroom():
                raise CommitUnknown('annotation_publication_pending_disk_low')
            try:
                fs.publish_replace(folder,'.head.json',_encode(dict(sequence=len(entries),entry_hash=entries[-1]['entry_hash'])),durable=True)
            except OSError:
                raise CommitUnknown('annotation_checkpoint_unknown') from None
            indexed = self.shelf.index().get(bundle.key)
            expected_dir = ulid.filename(bundle.title,p['annotation_id'],'md')[:-3]
            if indexed is not None and (type(indexed) is not dict or indexed.get('id') != p['annotation_id']
                                        or indexed.get('dir') != expected_dir):
                raise Corrupt('annotation_shelf_identity_conflict')
            result = self.shelf.ingest(bundle)
            if result.outcome not in codes.OK_OUTCOMES:
                raise CommitUnknown('annotation_publication_pending')
            if result.record_id != p['annotation_id'] or not self._published(entry,self._vault()):
                raise Corrupt('annotation_shelf_readback_failed')
            return Annotation(entry['sequence'],_encode(p))

    @staticmethod
    def _links(p, sources, entries):
        """Conservative validation includes reserved interpretations, not authority.

        Pending annotations cannot be used to bypass a cycle check. Actual
        confirmed labels are still derived separately with receipt verification.
        """
        source = sources[p['statement_id']]['payload']
        graph = {sid:list(row['payload']['repeat_of']) for sid,row in sources.items()}
        for entry in entries:
            prior = entry['payload']
            if prior['statement_id'] in graph:
                graph[prior['statement_id']] = list(prior['repeat_of'])
        if p['kind'] != 'correction' and p['repeat_of']:
            raise InvalidInput('annotation_repeat_requires_correction')
        for sid in p['repeat_of']:
            target = sources.get(sid)
            if (sid == p['statement_id'] or target is None
                    or target['payload']['scope_id'] != source['scope_id']
                    or _date(target['payload']['occurred_at']) > _date(source['occurred_at'])):
                raise InvalidInput('annotation_repeat_refused')
        graph[p['statement_id']] = p['repeat_of']
        stack = [(sid,1) for sid in p['repeat_of']]; visited = set(); work = 0
        while stack:
            sid,depth = stack.pop(); work += 1
            if sid == p['statement_id'] or depth > 64 or work > 10000:
                raise InvalidInput('annotation_repeat_cycle_or_limit')
            if sid not in graph:
                raise InvalidInput('annotation_repeat_ancestry_unavailable')
            if sid not in visited:
                visited.add(sid)
                stack.extend((target,depth+1) for target in graph.get(sid,()))
