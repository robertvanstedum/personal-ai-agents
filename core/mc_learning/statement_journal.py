"""Private owner-statement journal inside the existing memory root (S3a).

Only the action-service writer calls append, after current source/authority
checks and a durable DB intent. A PreparedStatement is data, not a credential.
File publication and the action receipt are NOT one transaction. Retry the same
span after an uncertain result; the original statement and labels are returned.

The bounded partition lock covers preparation, deduplication, integrity checks,
append and sync. Committed lines are immutable. A torn final fragment is saved
durably before removing ONLY that uncommitted fragment. Interior damage holds
the partition. Shelf sidecars are retryable projections, never approval events.
No live memory root or capture source is configured by this module.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo

import yaml

from core.memory_shelf import fsio, record, ulid
from core.memory_shelf.render import scrub_text
from core.minimoi_actions.context import _identifier
from core.minimoi_actions.receipts import _uuid
from core.minimoi_actions.requests import _object, _constant
from core.workshop_journal import fsutil as fs
from core.workshop_journal.errors import (CommitUnknown, Corrupt, IdConflict,
                                         InvalidInput, LockBusy, Missing)
from .statements import PreparedStatement, MAX_QUOTE_BYTES, MAX_TURN_BYTES

MAX_ENTRY = 32 * 1024
MAX_DAY = 16 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_DAYS = 3660
ZERO = '0' * 64
DAY = re.compile(r'\d{4}-\d{2}-\d{2}')
HASH = re.compile(r'[0-9a-f]{64}')
FIELDS = frozenset('schema statement_id span_key principal_id environment conversation_id turn_id '
    'source_record_id source_edition_hash scope_id speaker_id speaker_role start end quote range_hash '
    'fidelity redaction_count kind topic repeat_of repeat_standing proposing_actor proposing_model '
    'work_id occurred_at captured_at privacy_mode'.split())


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _decode(raw):
    return json.loads(raw.decode('utf-8'), object_pairs_hook=_object, parse_constant=_constant)


def _date(value):
    if type(value) is not str:
        raise ValueError()
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError()
    return stamp


def _scope(value):
    return type(value) is str and (value == 'robert' or
        (value.startswith('mandate:') and re.fullmatch(r'[0-9A-HJKMNP-TV-Z]{26}', value[8:])))


def validate_payload(raw):
    """Structural/integrity check only; authentic source reads remain upstream."""
    try:
        if type(raw) is not bytes or len(raw) > MAX_ENTRY:
            raise ValueError()
        p = _decode(raw)
        if type(p) is not dict or set(p) != FIELDS:
            raise ValueError()
        if (p['schema'] != 'mc-owner-statement/1' or not _uuid(p['statement_id'])
                or not _scope(p['scope_id']) or p['environment'] not in ('dev', 'prod')
                or p['privacy_mode'] != 'on_record' or p['speaker_role'] != 'owner'
                or p['speaker_id'] != p['principal_id'] or p['repeat_standing'] != 'proposed'
                or p['kind'] not in ('correction', 'direction', 'decision')
                or type(p['start']) is not int or type(p['end']) is not int
                or not 0 <= p['start'] < p['end'] <= MAX_TURN_BYTES
                or type(p['quote']) is not str or len(p['quote']) != p['end']-p['start']
                or len(p['quote'].encode()) > MAX_QUOTE_BYTES
                or type(p['topic']) is not str or len(p['topic']) > 160
                or type(p['redaction_count']) is not int or not 0 <= p['redaction_count'] <= 1048576
                or p['fidelity'] != ('redacted_source' if p['redaction_count'] else 'exact_scrubbed_source')
                or type(p['repeat_of']) is not list or len(p['repeat_of']) > 32
                or not all(_uuid(x) and x != p['statement_id'] for x in p['repeat_of'])
                or len(set(p['repeat_of'])) != len(p['repeat_of'])
                or (p['work_id'] is not None and not _uuid(p['work_id']))):
            raise ValueError()
        for key in ('principal_id', 'conversation_id', 'turn_id', 'source_record_id',
                    'proposing_actor', 'proposing_model'):
            if not _identifier(p[key]):
                raise ValueError()
        for key in ('span_key', 'range_hash', 'source_edition_hash'):
            if type(p[key]) is not str or not HASH.fullmatch(p[key]):
                raise ValueError()
        for key in ('quote', 'topic'):
            if scrub_text(p[key]) != (p[key], False):
                raise ValueError()
        identity = [p['principal_id'], p['environment'], p['conversation_id'], p['turn_id'],
                    p['source_edition_hash'], p['start'], p['end']]
        if (p['range_hash'] != _hash(p['quote'].encode()) or p['span_key'] != _hash(
                json.dumps(identity, ensure_ascii=True, separators=(',', ':')).encode('ascii'))
                or _date(p['occurred_at']) > _date(p['captured_at'])):
            raise ValueError()
        return p
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise InvalidInput('statement_payload_invalid') from None


@dataclass(frozen=True)
class AppendResult:
    statement_id: str
    commit_sequence: int
    entry_hash: str
    day: str
    duplicate: bool
    sidecar_ready: bool


class StatementJournal:
    def __init__(self, memory_root, *, principal_id, environment, scope_id,
                 timezone_name='America/Chicago', lock_timeout=2.0,
                 min_free_bytes=fsio.DEFAULT_MIN_FREE_BYTES):
        if (not _identifier(principal_id) or environment not in ('dev', 'prod') or not _scope(scope_id)
                or type(lock_timeout) not in (int, float) or not 0 < lock_timeout <= 5
                or type(min_free_bytes) is not int or min_free_bytes < 0):
            raise InvalidInput('statement_journal_config_invalid')
        self.root = Path(memory_root)
        self.principal, self.environment, self.scope = principal_id, environment, scope_id
        self.zone = ZoneInfo(timezone_name)
        self.timeout, self.min_free = lock_timeout, min_free_bytes
        self.partition = _hash(_encode([principal_id, environment, scope_id]))

    def _headroom(self):
        if fsio.check_headroom(self.root, min_free_bytes=self.min_free):
            raise InvalidInput('disk_low')

    @contextmanager
    def _locked(self, *, write):
        if write:
            self._headroom()
        fds = []
        try:
            # Existing approved root only; never invent a second memory root.
            fds.append(fs.open_root(str(self.root)))
            for name in ('turns', 'owner-statements', self.partition):
                parent = fds[-1]
                fds.append(fs.open_subdir(parent, name, create=write))
                if write:
                    fs.fsync_dir(parent)
            folder = fds[-1]
            lock = fs.open_file(folder, '.lock', (os.O_RDWR | os.O_CREAT) if write else os.O_RDONLY)
            fds.append(lock)
            end = time.monotonic()+self.timeout
            while True:
                try:
                    fcntl.flock(lock, (fcntl.LOCK_EX if write else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= end:
                        raise LockBusy('statement_lock_timeout') from None
                    time.sleep(min(0.01, max(0, end-time.monotonic())))
            fs.check_same_file(folder, '.lock', lock)
            if write:
                fs.fsync_dir(folder)
            yield folder
        finally:
            for fd in reversed(fds):
                os.close(fd)

    def _read(self, folder, name, limit):
        fd = fs.open_file(folder, name, os.O_RDONLY)
        try:
            size = os.fstat(fd).st_size
            if size > limit:
                raise Corrupt('statement_file_limit')
            pieces, offset = [], 0
            while offset < size:
                piece = os.pread(fd, min(size-offset, 1024*1024), offset)
                if not piece:
                    raise Corrupt('statement_file_changed')
                pieces.append(piece); offset += len(piece)
            if os.fstat(fd).st_size != size:
                raise Corrupt('statement_file_changed')
            fs.check_same_file(folder, name, fd)
            return b''.join(pieces)
        finally:
            os.close(fd)

    def _check_partition(self, payload):
        if (payload['principal_id'], payload['environment'], payload['scope_id']) != (
                self.principal, self.environment, self.scope):
            raise InvalidInput('statement_partition_mismatch')

    def _scan(self, folder, *, repair):
        names = sorted(n for n in os.listdir(folder) if n.endswith('.jsonl'))
        if len(names) > MAX_DAYS:
            raise Corrupt('statement_day_limit')
        rows, days, tails, total = [], {}, [], 0
        for name in names:
            if not DAY.fullmatch(name[:-6]):
                raise Corrupt('statement_day_invalid')
            raw = self._read(folder, name, MAX_DAY)
            total += len(raw)
            if total > MAX_TOTAL:
                raise Corrupt('statement_partition_limit')
            end = raw.rfind(b'\n')+1
            if end < len(raw):
                tails.append((name, raw[end:], end))
            committed = raw[:end]
            previous_sequence = 0
            for line in committed.splitlines():
                try:
                    if len(line) > MAX_ENTRY+1024:
                        raise ValueError()
                    row = _decode(line)
                    if (type(row) is not dict or set(row) != {'schema','sequence','previous_hash','payload','entry_hash'}
                            or row['schema'] != 'mc-statement-journal/1'
                            or type(row['sequence']) is not int or row['sequence'] <= previous_sequence
                            or _hash(_encode({k:v for k,v in row.items() if k != 'entry_hash'})) != row['entry_hash']):
                        raise ValueError()
                    p = validate_payload(_encode(row['payload']))
                    self._check_partition(p)
                    if _date(p['captured_at']).astimezone(self.zone).date().isoformat() != name[:-6]:
                        raise ValueError()
                    previous_sequence = row['sequence']
                    rows.append(row)
                except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, InvalidInput):
                    raise Corrupt('statement_committed_line_invalid') from None
            days[name[:-6]] = committed
        rows.sort(key=lambda x:x['sequence'])
        previous, spans, ids = ZERO, set(), set()
        for seq, row in enumerate(rows, 1):
            p = row['payload']
            if (row['sequence'] != seq or row['previous_hash'] != previous
                    or p['span_key'] in spans or p['statement_id'] in ids):
                raise Corrupt('statement_chain_invalid')
            spans.add(p['span_key']); ids.add(p['statement_id']); previous = row['entry_hash']
        # Durable high-water mark detects loss of a complete last day/line. A
        # larger verified prefix is allowed after append-before-checkpoint crash.
        try:
            head = _decode(self._read(folder, '.head.json', 2048))
        except Missing:
            head = None
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise Corrupt('statement_head_invalid') from None
        if head is not None:
            if (type(head) is not dict or set(head) != {'sequence','entry_hash'}
                    or type(head['sequence']) is not int or not 1 <= head['sequence'] <= len(rows)
                    or rows[head['sequence']-1]['entry_hash'] != head['entry_hash']):
                raise Corrupt('statement_head_mismatch')
        if tails and not repair:
            raise Corrupt('statement_tail_pending_recovery')
        # Check every committed line first, so corruption cannot trigger cleanup.
        for name, tail, end in tails:
            self._headroom()
            fs.publish_new(folder, f'.torn-{name}-{_hash(tail)}', tail)
            fd = fs.open_file(folder, name, os.O_RDWR)
            try:
                fs.truncate(fd, end)
                fs.full_sync(fd)
            finally:
                os.close(fd)
        return rows, days

    def snapshot(self):
        """Read-only verified committed snapshot. Missing/damage is NOT zero."""
        with self._locked(write=False) as folder:
            rows, _ = self._scan(folder, repair=False)
            return tuple(rows)

    def _sidecar(self, folder, day, raw, rows):
        identity_name = f'{day}.identity.json'
        try:
            identity = _decode(self._read(folder, identity_name, 2048))
        except Missing:
            raise Corrupt('statement_day_identity_missing') from None
        try:
            if (type(identity) is not dict or set(identity) != {'id','created'}
                    or type(identity['id']) is not str
                    or not re.fullmatch(r'[0-9A-HJKMNP-TV-Z]{26}', identity['id'])
                    or type(identity['created']) is not str or not identity['created'].endswith('Z')):
                raise ValueError()
            _date(identity['created'])
        except (ValueError, TypeError):
            raise Corrupt('statement_day_identity_invalid') from None
        selected = [r for r in rows if _date(r['payload']['captured_at']).astimezone(self.zone).date().isoformat() == day]
        meta = dict(identity, kind='owner-statements', chair='MC',
            source=f'turns/owner-statements/{self.partition}/{day}.jsonl',
            source_hash=_hash(raw), scope=self.scope, tier='raw', tags=['owner-statements'], events=[],
            statement_count=len(selected), through_commit_sequence=max((r['sequence'] for r in selected), default=0))
        record.check(meta)
        fs.publish_replace(folder, f'{day}.meta.yml', yaml.safe_dump(meta, sort_keys=False,
                           allow_unicode=True).encode(), durable=True)

    def append(self, prepared):
        if type(prepared) is not PreparedStatement:
            raise InvalidInput('statement_prepared_required')
        payload = validate_payload(prepared.encoded)
        self._check_partition(payload)
        try:
            with self._locked(write=True) as folder:
                rows, days = self._scan(folder, repair=True)
                intent_name = f'.intent-{payload["span_key"]}.json'
                try:
                    original = validate_payload(self._read(folder, intent_name, MAX_ENTRY))
                    self._check_partition(original)
                    # Changed classifications/providers are annotations, not a rewrite.
                    mutable = {'statement_id','kind','topic','repeat_of','proposing_actor',
                               'proposing_model','captured_at','work_id'}
                    if any(original[k] != payload[k] for k in FIELDS-mutable):
                        raise IdConflict('statement_span_conflict')
                    payload = original
                except Missing:
                    if any(r['payload']['span_key'] == payload['span_key'] for r in rows):
                        raise Corrupt('statement_intent_missing') from None
                    # UUID reuse across different spans must not alias a receipt.
                    fs.publish_new(folder, f'.id-{payload["statement_id"]}', payload['span_key'].encode())
                    fs.publish_new(folder, intent_name, _encode(payload))
                existing = next((r for r in rows if r['payload']['span_key'] == payload['span_key']), None)
                if existing and existing['payload'] != payload:
                    raise Corrupt('statement_intent_mismatch')
                day = _date(payload['captured_at']).astimezone(self.zone).date().isoformat()
                if not existing:
                    if any(r['payload']['statement_id'] == payload['statement_id'] for r in rows):
                        raise IdConflict('statement_id_conflict')
                    identity_name = f'{day}.identity.json'
                    try:
                        self._read(folder, identity_name, 2048)
                    except Missing:
                        if day in days:
                            raise Corrupt('statement_day_identity_missing') from None
                        stamp = _date(payload['captured_at']).astimezone(timezone.utc)
                        fs.publish_new(folder, identity_name, _encode(dict(
                            id=ulid.new_ulid(int(stamp.timestamp()*1000)), created=stamp.isoformat().replace('+00:00','Z'))))
                    row = dict(schema='mc-statement-journal/1', sequence=len(rows)+1,
                        previous_hash=rows[-1]['entry_hash'] if rows else ZERO, payload=payload)
                    row['entry_hash'] = _hash(_encode(row))
                    line = _encode(row)+b'\n'
                    if (len(days.get(day,b''))+len(line) > MAX_DAY
                            or sum(map(len,days.values()))+len(line) > MAX_TOTAL):
                        raise InvalidInput('statement_storage_limit')
                    self._headroom()
                    name = f'{day}.jsonl'
                    fd = fs.open_file(folder, name, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
                    try:
                        fs.check_same_file(folder, name, fd)
                        fs.write_all(fd, line)
                        fs.full_sync(fd)
                        fs.fsync_dir(folder)
                    finally:
                        os.close(fd)
                    rows.append(row); days[day] = days.get(day,b'')+line
                else:
                    row = existing
                    # An earlier fsync failure may have left complete visible bytes.
                    fd = fs.open_file(folder, f'{day}.jsonl', os.O_RDONLY)
                    try:
                        fs.full_sync(fd)
                        fs.fsync_dir(folder)
                    finally:
                        os.close(fd)
                try:
                    self._headroom()
                except InvalidInput:
                    raise CommitUnknown('statement_checkpoint_pending') from None
                fs.publish_replace(folder, '.head.json', _encode(dict(
                    sequence=rows[-1]['sequence'], entry_hash=rows[-1]['entry_hash'])), durable=True)
                sidecar_ready = True
                try:
                    self._sidecar(folder, day, days[day], rows)
                except (OSError, Corrupt, ValueError):
                    sidecar_ready = False
                return AppendResult(payload['statement_id'], row['sequence'], row['entry_hash'],
                                    day, existing is not None, sidecar_ready)
        except OSError:
            # May include complete durable append. Never claim rollback across stores.
            raise CommitUnknown('statement_append_unconfirmed') from None
