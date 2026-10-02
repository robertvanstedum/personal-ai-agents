"""Disposable file-first fixture store with immutable generations and atomic HEAD.

All writes take one process lock. Readers use the same lock and authoritative
files, never a stale catalog. This is L0 convention, not same-user isolation.
"""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import os
from pathlib import Path
import shutil
import tempfile

from .access import can_read
from .adapters import FixtureSessionAdapter
from .contracts import (VERSION, CLASSES, ContractError, Unavailable, canonical, digest,
                        strict_json, record_hash, seal, stable_id, timestamp, validate, now)


def _write(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _sync(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _empty():
    return dict(schema_version=VERSION, synthetic=True, records={}, relations={},
                designations={}, receipts=[], tombstones={}, blocked_events=[], deletion_plans=[], source_classes={})


def _source_key(provenance):
    return digest(canonical([provenance[k] for k in ('provider', 'thread_id', 'event_id')]))


class Store:
    def __init__(self, root, authority):
        path = Path(root)
        if not path.is_absolute():
            raise ContractError('Output root must be explicit and absolute')
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ContractError('Output path cannot contain symlinks')
        self.root = path
        self.authority = authority
        # Real-vault activation is deliberately not implemented in P0–P2.
        if not any(path.is_relative_to(Path(p).resolve()) for p in ('/private/tmp', tempfile.gettempdir())):
            raise Unavailable('P0–P2 stores must be disposable directories beneath the system temporary directory')

    def initialize(self):
        if self.root.exists():
            raise ContractError('Output root must not already exist')
        self.root.mkdir(mode=0o700)
        (self.root / 'generations').mkdir(mode=0o700)
        (self.root / 'schemas').mkdir(mode=0o700)
        for path in (Path(__file__).parent / 'schemas').glob('*.json'):
            _write(self.root / 'schemas' / path.name, path.read_bytes())
        _write(self.root / 'FIXTURE_ONLY', b'Synthetic disposable store. No encryption, backup or live activation.\n')
        with self._lock():
            self._publish(_empty(), {}, now())

    @contextmanager
    def _lock(self):
        if not self.root.is_dir() or self.root.is_symlink() or self.root.stat().st_mode & 0o077:
            raise ContractError('Owner-private initialized fixture root required')
        if not (self.root / 'FIXTURE_ONLY').is_file():
            raise ContractError('Not an initialized fixture store')
        # Refuse symlinks anywhere: no paths supplied in metadata are followed.
        if any(p.is_symlink() for p in self.root.rglob('*')):
            raise ContractError('Symlink in fixture store')
        fd = os.open(self.root / '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _load(self):
        generation = (self.root / 'CURRENT').read_text()
        if len(generation) != 64 or any(c not in '0123456789abcdef' for c in generation):
            raise ContractError('Invalid generation pointer')
        base = self.root / 'generations' / generation
        manifest = strict_json((base / 'manifest.json').read_bytes())
        validate('manifest', manifest)
        if manifest['generation'] != generation or digest(canonical({k:v for k,v in manifest.items() if k != 'generation'})) != generation:
            raise ContractError('Manifest identity mismatch')
        blobs = {}
        for item in manifest['files']:
            name = item['path']
            if name != 'state.json' and not (name.startswith('blobs/') and len(name) == 70 and all(c in '0123456789abcdef' for c in name[6:])):
                raise ContractError('Unsafe manifest path')
            raw = (base / name).read_bytes()
            if digest(raw) != item['sha256'] or len(raw) != item['size']:
                raise ContractError('Snapshot integrity failure')
            if name.startswith('blobs/'):
                blobs[name[6:]] = raw
        state = strict_json((base / 'state.json').read_bytes())
        if digest(canonical(state)) != manifest['state_hash'] or state.get('schema_version') != VERSION:
            raise ContractError('State version or hash mismatch')
        self._validate_state(state, blobs)
        return state, blobs, manifest

    def _validate_state(self, state, blobs):
        for designation in state['designations'].values():
            validate('designation', designation)
        for record in state['records'].values():
            validate('record', record)
            if record['disclosure_class'] == 'owner-only' and (record['text'] or record['sources'] or record['data'] != {'stub': True}):
                raise ContractError('Owner-only plaintext ingestion is disabled')
            for source in record['sources']:
                self._anchor(state, source)
            if record['type'] == 'source' and record['disclosure_class'] != 'owner-only':
                if record['data']['raw_hash'] not in blobs:
                    raise ContractError('Source bytes missing')
        for relation in state['relations'].values():
            validate('relation', relation)
            if any(relation[k] not in state['records'] and relation[k] not in state['tombstones'] for k in ('from_id','to_id')):
                raise ContractError('Relation endpoint missing')

    @staticmethod
    def _anchor(state, anchor):
        record = state['records'].get(anchor['record_id'])
        if record is None or record['hash'] != anchor['record_hash']:
            raise ContractError('Citation source missing or changed')
        if not 0 <= anchor['start'] <= anchor['end'] <= len(record['text']):
            raise ContractError('Citation offsets outside exact source')
        return record

    def _publish(self, state, blobs, captured_at, fault=lambda stage: None):
        timestamp(captured_at)
        self._validate_state(state, blobs)
        # Only referenced raw bytes survive into a new authoritative generation.
        needed = {r['data']['raw_hash'] for r in state['records'].values() if r['type'] == 'source' and r['disclosure_class'] != 'owner-only'}
        files = {'state.json': canonical(state), **{'blobs/' + h: blobs[h] for h in needed}}
        dependencies = {r['id']: [s['record_id'] for s in r['sources']] for r in state['records'].values()}
        manifest = dict(schema_version=VERSION, captured_at=captured_at,
                        files=[dict(path=p, sha256=digest(raw), size=len(raw)) for p,raw in sorted(files.items())],
                        dependencies=dependencies, state_hash=digest(files['state.json']), synthetic=True)
        generation = digest(canonical(manifest))
        manifest['generation'] = generation
        validate('manifest', manifest)
        scratch = Path(tempfile.mkdtemp(prefix='.pending-', dir=self.root))
        try:
            (scratch / 'blobs').mkdir(mode=0o700)
            for name, raw in files.items():
                _write(scratch / name, raw)
            _write(scratch / 'manifest.json', canonical(manifest))
            _sync(scratch / 'blobs')
            _sync(scratch)
            fault('before_publish')
            destination = self.root / 'generations' / generation
            if destination.exists():
                raise ContractError('Unexpected existing generation')
            os.rename(scratch, destination)
            _sync(destination.parent)
            # All catalog copies are disposable and removed before changing HEAD.
            self._remove_catalogs()
            pointer = self.root / '.next'
            if pointer.exists():
                pointer.unlink()
            _write(pointer, generation.encode())
            os.replace(pointer, self.root / 'CURRENT')
            _sync(self.root)
            fault('after_publish')
        finally:
            if scratch.exists():
                shutil.rmtree(scratch)
        return manifest

    def _remove_catalogs(self):
        for path in self.root.glob('catalog*'):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    def inspect(self):
        with self._lock():
            state, _, manifest = self._load()
            return dict(synthetic=True, generation=manifest['generation'], records=len(state['records']),
                        relations=len(state['relations']), tombstones=len(state['tombstones']),
                        backup_erasure='not_verified', live_activation='disabled')

    def capture(self, source_path, designation, context, receipt, *, apply=False, captured_at=None, fault=lambda stage: None):
        p = self.authority.principal(context)
        validate('designation', designation)
        owner = self.authority.receipt(receipt, 'designate', designation['hash'])
        if designation['topic'] not in p.topics or designation['designated_by'] != p.name:
            raise ContractError('Designation is outside authenticated scope')
        if designation['disclosure_class'] == 'owner-only':
            raise Unavailable('Owner-only ingestion needs proven key custody and encryption; disabled')
        if CLASSES.index(designation['disclosure_class']) > CLASSES.index(p.clearance):
            raise ContractError('Capture clearance exceeded')
        export = FixtureSessionAdapter().read(Path(source_path))
        thread = [e for e in export.events if e.value['provider'] == designation['provider'] and e.value['thread_id'] == designation['thread_id']]
        origin = [e for e in thread if e.value['event_id'] == designation['owner_event_id']]
        if len(origin) != 1 or origin[0].value['origin'] != 'owner' or origin[0].value['author'] != owner['principal'] or origin[0].value['private']:
            raise ContractError('Designation must reference a direct nonprivate owner-origin event')
        start, stop = designation['start'], designation['stop']
        if stop is not None and stop <= start:
            raise ContractError('Designation needs a nonempty half-open range')
        selected = [e for e in thread if e.value['index'] >= start and (stop is None or e.value['index'] < stop) and not e.value['private']]
        if not selected:
            raise ContractError('No eligible events in designated range')
        # No external attachments are read in this slice. Authorized missing bytes
        # remain named gaps; undesignated/private event attachments are never used.
        gaps = list(export.gaps)
        gaps += ['attachments_unavailable' for e in selected if e.value['attachments']]
        gaps += ['range_completeness_unverified', 'private_events_excluded']
        captured_at = captured_at or now()
        timestamp(captured_at)
        with self._lock():
            state, blobs, _ = self._load()
            previous = state['designations'].get(designation['id'])
            if previous and previous != designation:
                raise ContractError('Designation revision requires a new identity and exact owner receipt')
            new = []
            for event in selected:
                e = event.value
                identity = [e[k] for k in ('provider', 'thread_id', 'event_id')]
                raw_hash = digest(event.raw)
                rid = stable_id([*identity, raw_hash, designation['hash']])
                if digest(canonical(identity)) in state['blocked_events']:
                    raise ContractError('Deleted source identity cannot be recaptured')
                identity_hash = digest(canonical(identity))
                effective_class = max([designation['disclosure_class'], state['source_classes'].get(identity_hash,'team')],key=CLASSES.index)
                if CLASSES.index(effective_class) > CLASSES.index(p.clearance):
                    raise ContractError('Effective source class exceeds capture clearance')
                if rid in state['records']:
                    continue
                state['source_classes'][identity_hash] = effective_class
                r = seal(dict(schema_version=VERSION,id=rid,type='source',created_at=captured_at,
                    event_time=e['event_time'],author=e['author'],disclosure_class=effective_class,
                    topics=[designation['topic']],sources=[],text=e['text'],authority='evidence',
                    provenance=dict(provider=e['provider'],thread_id=e['thread_id'],event_id=e['event_id'],
                        capture_method='synthetic-jsonl/exact-line',extractor_version='fixture/1.0',model=None,
                        fidelity='relayed' if e['origin'] in ('relay','quote') else 'direct',gaps=sorted(set(gaps))),
                    data=dict(raw_hash=raw_hash,original_file_hash=export.original_hash,designation_id=designation['id'],
                              event_index=e['index'],completeness='partial',attachments_authorized=[h for h in e['attachments'] if h in designation['attachments']])))
                validate('record', r)
                state['records'][rid] = r
                blobs[raw_hash] = event.raw
                new.append(rid)
            result = dict(new_records=len(new), record_ids=new, gaps=sorted(set(gaps)), applied=False)
            if apply and new:
                state['designations'][designation['id']] = designation
                state['receipts'].append(owner)
                self._publish(state, blobs, captured_at, fault)
                result['applied'] = True
            return result

    def add_fixture_stub(self, record, context):
        """Metadata-only fixture: never accepts restricted plaintext or keys."""
        p = self.authority.principal(context)
        validate('record',record)
        if not p.owner or record['type'] != 'source' or record['disclosure_class'] != 'owner-only' or record['text'] or record['sources'] or record['data'] != {'stub':True}:
            raise ContractError('Only owner-authored metadata stubs are accepted')
        if record['author'] != p.name or not set(record['topics']).issubset(p.topics):
            raise ContractError('Stub scope mismatch')
        # Caller must supply only publicly scoped topics; provenance is fixed so
        # restricted paths, titles and participant metadata cannot enter indexes.
        expected = dict(provider='restricted-fixture',thread_id='withheld',event_id='withheld',capture_method='stub-only',extractor_version='1',model=None,fidelity='observed',gaps=[])
        if record['provenance'] != expected:
            raise ContractError('Restricted stub metadata must be minimal')
        with self._lock():
            state,blobs,_ = self._load()
            if record['id'] in state['records'] or record['id'] in state['tombstones']:
                raise ContractError('Stub identity already used')
            state['records'][record['id']] = record
            self._publish(state,blobs,now())

    def add_record(self, record, context, *, receipt=None):
        p = self.authority.principal(context)
        validate('record', record)
        if record['author'] != p.name or not set(record['topics']).issubset(p.topics) or not record['topics']:
            raise ContractError('Record author or topics outside authenticated scope')
        if record['type'] in ('source','designation'):
            raise ContractError('Use designated capture for source records')
        if record['disclosure_class'] == 'owner-only':
            raise Unavailable('Owner-only content ingestion disabled')
        if not record['sources']:
            raise ContractError('Derived records need exact source citations')
        with self._lock():
            state, blobs, _ = self._load()
            if record['id'] in state['records'] or record['id'] in state['tombstones']:
                raise ContractError('Record identity already used; revisions need a new identity')
            source_records = [self._anchor(state, a) for a in record['sources']]
            if any(not can_read(p, r, 'local') for r in source_records):
                raise ContractError('Source outside authenticated scope')
            expected = max([record['disclosure_class'], *[r['disclosure_class'] for r in source_records]], key=CLASSES.index)
            if record['disclosure_class'] != expected or CLASSES.index(expected) > CLASSES.index(p.clearance):
                raise ContractError('Derivative must inherit strictest contributing class')
            if record['authority'] == 'owner':
                state['receipts'].append(self.authority.receipt(receipt, 'promote', record['hash']))
            elif record['type'] in ('lesson','practice'):
                if record['authority'] != 'pending':
                    raise ContractError('Consequential promotion must remain pending')
            elif record['type'] == 'decision' and record['authority'] not in ('agent_proposal','pending'):
                raise ContractError('Decision must remain an agent proposal')
            elif record['authority'] not in ('pending','agent_proposal','assertion'):
                raise ContractError('Derived assertion cannot claim source authority')
            state['records'][record['id']] = deepcopy(record)
            self._publish(state, blobs, now())

    def add_relation(self, relation, context, *, receipt=None):
        p = self.authority.principal(context)
        validate('relation', relation)
        if relation['author'] != p.name:
            raise ContractError('Relation author mismatch')
        with self._lock():
            state, blobs, _ = self._load()
            ends = [state['records'].get(relation[k]) for k in ('from_id','to_id')]
            if any(r is None or not can_read(p,r,'local') for r in ends):
                raise ContractError('Relation endpoints outside authenticated scope')
            if relation['id'] in state['relations'] or relation['from_id'] == relation['to_id']:
                raise ContractError('Invalid relation identity')
            if relation['disclosure_class'] != max((r['disclosure_class'] for r in ends), key=CLASSES.index):
                raise ContractError('Relation class must inherit both endpoints')
            if relation['authority'] == 'owner':
                state['receipts'].append(self.authority.receipt(receipt,'relation',relation['hash']))
            state['relations'][relation['id']] = deepcopy(relation)
            self._publish(state, blobs, now())

    @staticmethod
    def _closure(state, ids):
        affected = set(ids)
        while True:
            extra = {r['id'] for r in state['records'].values() if any(s['record_id'] in affected for s in r['sources'])}
            if extra <= affected:
                return affected
            affected |= extra

    def change_class(self, rid, new_class, context, receipt):
        p = self.authority.principal(context)
        if not p.owner or new_class not in CLASSES:
            raise ContractError('Owner classification required')
        if new_class == 'owner-only':
            raise Unavailable('Owner-only reclassification requires encryption; delete or keep activation disabled')
        with self._lock():
            state, blobs, _ = self._load()
            record = state['records'].get(rid)
            if record is None:
                raise ContractError('Unknown classification target')
            request = digest(canonical(dict(id=rid,hash=record['hash'],disclosure_class=new_class)))
            accepted = self.authority.receipt(receipt,'classify',request)
            if CLASSES.index(new_class) < CLASSES.index(record['disclosure_class']):
                raise ContractError('Declassification is deferred; requires a separately reviewed rebuild')
            duplicates = {rid}
            if record['type'] == 'source':
                duplicates |= {i for i,r in state['records'].items() if r['type'] == 'source' and
                               _source_key(r['provenance']) == _source_key(record['provenance'])}
                state['source_classes'][_source_key(record['provenance'])] = new_class
            affected = self._closure(state,duplicates)
            # Update hashes in dependency order; source and derived citations
            # continue to identify the exact current classed revision.
            pending = set(affected)
            while pending:
                ready = [i for i in pending if not any(a['record_id'] in pending for a in state['records'][i]['sources'])]
                if not ready:
                    raise ContractError('Cyclic dependencies')
                for i in ready:
                    r = state['records'][i]
                    r['disclosure_class'] = max([r['disclosure_class'],new_class],key=CLASSES.index)
                    for a in r['sources']:
                        a['record_hash'] = state['records'][a['record_id']]['hash']
                    # Any changed revision loses earlier owner promotion.
                    if r['authority'] == 'owner':
                        r['authority'] = 'pending'
                    state['records'][i] = seal(r)
                    pending.remove(i)
            for i,r in state['relations'].items():
                if r['from_id'] in affected or r['to_id'] in affected:
                    r['disclosure_class'] = max((state['records'][r[k]]['disclosure_class'] for k in ('from_id','to_id') if r[k] in state['records']),key=CLASSES.index)
                    r['authority'] = 'pending'
                    state['relations'][i] = seal(r)
            state['receipts'].append(accepted)
            self._publish(state,blobs,now())
            return sorted(affected)

    def deletion_plan(self, rid):
        with self._lock():
            state, _, _ = self._load()
            return self._deletion_plan(state,rid)

    def _deletion_plan(self,state,rid):
        if rid not in state['records']:
            raise ContractError('Unknown deletion target')
        target = state['records'][rid]
        duplicates = {rid}
        if target['type'] == 'source':
            duplicates |= {i for i,r in state['records'].items() if r['type'] == 'source' and
                           (_source_key(r['provenance']) == _source_key(target['provenance']) or
                            r['data'].get('raw_hash') == target['data'].get('raw_hash'))}
        affected = self._closure(state,duplicates)
        # Full generations contain overlapping copies; conservatively purge all
        # superseded generations. Retained independent sources are republished.
        paths = sorted(str(p.relative_to(self.root)) for p in (self.root/'generations').rglob('*') if p.is_file())
        paths += sorted(str(p.relative_to(self.root)) for p in self.root.glob('catalog*') if p.is_file())
        payload = dict(id=rid,records=sorted(affected),hashes={i:state['records'][i]['hash'] for i in sorted(affected)},paths=paths)
        return {**payload,'hash':digest(canonical(payload))}

    def delete(self, rid, context, receipt, *, fault=lambda stage: None):
        p = self.authority.principal(context)
        if not p.owner:
            raise ContractError('Owner deletion required')
        with self._lock():
            state, blobs, _ = self._load()
            plan = self._deletion_plan(state,rid)
            accepted = self.authority.receipt(receipt,'delete',plan['hash'])
            date = now()
            for i in plan['records']:
                r = state['records'].pop(i)
                if r['type'] == 'source':
                    state['blocked_events'].append(_source_key(r['provenance']))
                state['tombstones'][i] = dict(id=i,hash=r['hash'],reason='owner_authorized_deletion',date=date,
                    manifest_hash=plan['hash'],backup_erasure='pending',working_erasure='pending')
            # Relation ids point at tombstone ids; they remain unavailable to
            # retrieval because both live endpoints are required.
            state['receipts'].append(accepted)
            state['deletion_plans'].append(plan)
            manifest = self._publish(state,blobs,date)
            fault('after_tombstone')
            self._purge_old(manifest['generation'])
            # Exact structured path manifest is retained without source text.
            _write(self.root / ('deletion-' + plan['hash'] + '.json'), canonical(plan))
            return {**plan,'working_erasure':'verified_paths_removed','backup_erasure':'pending'}

    def _purge_old(self,generation):
        for path in (self.root/'generations').iterdir():
            if path.name != generation:
                shutil.rmtree(path)
        for path in self.root.glob('.pending-*'):
            shutil.rmtree(path)
        self._remove_catalogs()
        _sync(self.root/'generations')

    def recover_erasure(self,context):
        if not self.authority.principal(context).owner:
            raise ContractError('Owner required')
        with self._lock():
            state,_,manifest=self._load()
            if not state['tombstones']:
                return {'working_erasure':'no_pending_deletion'}
            self._purge_old(manifest['generation'])
            return {'working_erasure':'old_generations_removed','backup_erasure':'pending'}
