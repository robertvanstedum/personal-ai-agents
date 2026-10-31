"""Reversible session-list archive, independent of recording/meeting state.

No data removal, close, pause, runtime execution or automatic scheduler.
External portal work must be checked by an authoritative server-side adapter;
its absence is unknown, never clear. The HTTP body supplies no such proof.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import sqlite3
import re
from contextvars import ContextVar
from domains.records.store import Problem
from domains.records.meetings import TERMINAL

DAYS = 30
VERIFIED_EXTERNAL = ContextVar('records_archive_external', default=None)


def portal_proof(data, room):
    expected={'kind','room','state','checked_at','room_version','latest_seq','mapping_sha256','request_count'}
    if (not isinstance(data,dict) or set(data) not in (expected,expected|{'scheduled'}) or data.get('kind')!='portal_locked_archive_v1'
            or data.get('room')!=room or data.get('state')!='clear'
            or type(data.get('room_version')) is not int or data['room_version']<1
            or type(data.get('latest_seq')) is not int or data['latest_seq']<0
            or type(data.get('request_count')) is not int or not 0<=data['request_count']<=1000
            or not isinstance(data.get('mapping_sha256'),str)
            or not re.fullmatch('[0-9a-f]{64}',data['mapping_sha256'])):
        raise Problem('Verified portal archive evidence required',409)
    moment(data.get('checked_at'))
    if 'scheduled' in data:scheduled_deadline(data['scheduled'],moment(data['checked_at']))
    return data



def scheduled_deadline(data,now):
    """Only trusted HMAC portal proof may supply this optional batch marker."""
    from zoneinfo import ZoneInfo
    if (not isinstance(data,dict) or set(data)!={'purpose','delegation_id','run_id','deadline'}
            or data.get('purpose')!='rooms.archive.inactive30.v1'
            or any(not isinstance(data.get(k),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',data[k]) for k in ('delegation_id','run_id'))):
        raise Problem('Scheduled archive proof invalid',409)
    deadline=moment(data['deadline']);local=now.astimezone(ZoneInfo('America/Chicago'))
    expected=local.replace(hour=6,minute=0,second=0,microsecond=0)
    if local.hour>=6 or deadline!=expected or now>=deadline:
        raise Problem('Scheduled archive window ended',409)
    return deadline


def moment(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (AttributeError, ValueError, TypeError):
        raise Problem('Activity timestamp unavailable', 409)
    if result.tzinfo is None:
        raise Problem('Activity timestamp unavailable', 409)
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class ExternalWorkEvidence:
    room: str
    state: str  # clear | pending | unavailable
    checked_at: datetime
    room_version: int
    latest_seq: int


class Archive:
    def __init__(self, store, *, external_pending=None, clock=None):
        self.store = store
        self.external_pending = external_pending
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS room_archive(
                room TEXT PRIMARY KEY REFERENCES rooms(id), version INTEGER NOT NULL,
                archived_at TEXT, archived_by TEXT, last_activity TEXT,
                through_seq INTEGER, restored_at TEXT, restored_by TEXT)''')

    def _snapshot(self, db, room):
        row = db.execute('SELECT * FROM rooms WHERE id=?', (room,)).fetchone()
        if row is None:
            raise Problem('Session not found', 404)
        item = dict(row)
        meta = db.execute('SELECT * FROM room_archive WHERE room=?', (room,)).fetchone()
        item['archive_version'] = meta['version'] if meta else 0
        item['archived_at'] = meta['archived_at'] if meta else None
        item['latest_seq'] = db.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE room=?', (room,)).fetchone()[0]
        return item

    def _backend_work(self, db, item, now):
        try:
            marks = ','.join('?' for _ in TERMINAL)
            if db.execute(f'SELECT 1 FROM turns WHERE room=? AND state NOT IN ({marks}) LIMIT 1',
                          (item['id'], *TERMINAL)).fetchone():
                return 'pending'
            if db.execute("SELECT 1 FROM cos_requests WHERE room=? AND state NOT IN ('committed','cancelled','superseded') LIMIT 1", (item['id'],)).fetchone():
                return 'pending'
            if db.execute("SELECT 1 FROM coordination_items WHERE room=? AND state NOT IN ('acknowledged','cancelled') LIMIT 1", (item['id'],)).fetchone():
                return 'pending'
            mode=db.execute('SELECT * FROM cos_auto WHERE room=? AND enabled=1', (item['id'],)).fetchone()
            if mode and item['state']=='active' and moment(mode['expires'])>now and mode['remaining']>0:
                # A mode bit is not pending work. Preserve a still-authorized,
                # unscheduled owner trigger; later new activity restores visibility.
                if db.execute("SELECT 1 FROM events WHERE room=? AND actor='robert' AND kind='message' AND origin IS NULL AND seq>? LIMIT 1",(item['id'],mode['cursor'])).fetchone():
                    return 'pending'
            meeting = db.execute('SELECT window_expires FROM meetings WHERE room=?', (item['id'],)).fetchone()
            if meeting and item['state'] == 'active' and moment(meeting['window_expires']) > now:
                return 'pending'
        except (sqlite3.DatabaseError, Problem):
            return 'unavailable'
        return 'clear'

    def _external_work(self, item, now):
        if self.external_pending is None and VERIFIED_EXTERNAL.get() is None:
            return 'unavailable'
        try:
            if self.external_pending is not None:
                proof = self.external_pending(item['id'], item['version'], item['latest_seq'])
            else:
                data=VERIFIED_EXTERNAL.get()
                proof=ExternalWorkEvidence(data['room'],data['state'],moment(data['checked_at']),
                                           data['room_version'],data['latest_seq'])
            observed = self.clock()
            if (not isinstance(proof, ExternalWorkEvidence) or proof.room != item['id']
                    or proof.room_version != item['version'] or proof.latest_seq != item['latest_seq']
                    or proof.checked_at.tzinfo is None
                    or not timedelta(0) <= observed - proof.checked_at <= timedelta(seconds=15)
                    or proof.state not in {'clear','pending','unavailable'}):
                return 'unavailable'
            return proof.state
        except Exception:
            return 'unavailable'  # Never retain credentials or raw transport errors.

    def _assessment(self, db, item, now):
        try:
            activity = moment(item['updated'])
            if activity > now:
                raise Problem('Activity timestamp unavailable', 409)
            old = now - activity >= timedelta(days=DAYS)
        except Problem:
            return {'eligible':False, 'reason':'activity_unavailable', 'backend_work':'unavailable', 'external_work':'unavailable'}
        backend = self._backend_work(db, item, now)
        external = self._external_work(item, now)
        reason = ('already_archived' if item['archived_at'] else
                  'recent_activity' if not old else
                  'backend_work_'+backend if backend != 'clear' else
                  'external_work_'+external if external != 'clear' else 'eligible')
        return {'eligible':reason=='eligible', 'reason':reason,
                'backend_work':backend, 'external_work':external}

    def reconcile(self, actor, room, payload):
        self.store.owner(actor)
        if not isinstance(payload,dict) or set(payload)!={'operation_key','checked_at','payload'} or not isinstance(payload['operation_key'],str) or not 1<=len(payload['operation_key'])<=160:
            raise Problem('Archive attempt proof required')
        checked=moment(payload['checked_at'])
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.platform_access.enforce(db,actor,None,'admin')
            # A signed external proof is valid for only15seconds inside the
            # write transaction. At30seconds, this barrier either sees its
            # committed receipt or ensures no older queued archive can commit.
            if self.clock()-checked<timedelta(seconds=30):
                raise Problem('Archive attempt still uncertain; check again later',409)
            row=db.execute('SELECT response,request_hash FROM operations WHERE actor=? AND key=?',(actor,payload['operation_key'])).fetchone()
            if row:
                from domains.records.store import digest, canonical
                expected=digest(canonical({'op':'room_archive','room':room,'payload':payload['payload']}).encode())
                if row['request_hash']!=expected:return {'state':'conflict'}
                import json
                response=json.loads(row['response'])
                result=response.get('result',{})
                if not isinstance(result,dict) or result.get('room')!=room or result.get('archived') is not True:
                    raise Problem('Operation is not this archive attempt',409)
                return {'state':'committed','receipt':response}
            return {'state':'not_committed'}

    def work_snapshot(self, actor, room):
        self.store.owner(actor)
        with self.store.connect() as db:
            db.execute('BEGIN')
            self.store.access(db,actor,room)
            item=self._snapshot(db,room)
            seqs=[r[0] for r in db.execute("SELECT seq FROM events WHERE room=? AND actor='robert' AND kind='message' ORDER BY seq LIMIT 1001",(room,))]
            if len(seqs)>1000:raise Problem('Archive history requires bounded reconciliation',409)
            return {'room':room,'version':item['version'],'archive_version':item['archive_version'],
                    'latest_seq':item['latest_seq'],'last_activity':item['updated'],
                    'archived_at':item['archived_at'],'owner_message_seqs':seqs}

    def preview(self, actor):
        self.store.owner(actor)
        now = self.clock()
        with self.store.connect() as db:
            db.execute('BEGIN')
            self.store.platform_access.enforce(db, actor, None, 'admin')
            identities = db.execute('SELECT id FROM rooms ORDER BY updated DESC LIMIT 201').fetchall()
            if len(identities)>200:
                raise Problem('Archive preview requires a bounded selection', 409)
            result=[]
            for row in identities:
                item=self._snapshot(db,row['id'])
                result.append({'id':item['id'], 'version':item['version'],
                    'archive_version':item['archive_version'], 'latest_seq':item['latest_seq'],
                    'last_activity':item['updated'], 'archived_at':item['archived_at'],
                    **self._assessment(db,item,now)})
            return {'threshold_days':DAYS, 'observed_at':now.isoformat(), 'rooms':result}

    def change(self, actor, key, room, payload, *, restore=False):
        self.store.owner(actor)
        required={'version','archive_version','latest_seq','last_activity'}
        if not isinstance(payload,dict) or set(payload)!=required:
            raise Problem('Send exact activity and version proof')
        if (any(type(payload[n]) is not int or payload[n] < (1 if n=='version' else 0)
                for n in ('version','archive_version','latest_seq'))
                or not isinstance(payload['last_activity'],str)):
            raise Problem('Invalid activity and version proof')
        def action(db):
            item=self._snapshot(db,room)
            expected={'version':item['version'], 'archive_version':item['archive_version'],
                      'latest_seq':item['latest_seq'], 'last_activity':item['updated']}
            if payload != expected:
                raise Problem('Session activity changed; refresh before archiving',409)
            at=self.clock()
            if restore:
                if not item['archived_at']:
                    raise Problem('Session is not archived',409)
            else:
                # Re-evaluate activity and backend work under the same SQLite
                # write transaction as metadata publication; preview is no grant.
                check=self._assessment(db,item,at)
                if not check['eligible']:
                    raise Problem('Archive refused: '+check['reason'],409)
            external=VERIFIED_EXTERNAL.get()
            if not restore and isinstance(external,dict) and 'scheduled' in external:
                scheduled_deadline(external['scheduled'],self.clock())
            stamp=at.isoformat(); revision=item['archive_version']+1
            db.execute('''INSERT INTO room_archive(room,version,archived_at,archived_by,last_activity,through_seq,restored_at,restored_by)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(room) DO UPDATE SET
                version=excluded.version,archived_at=excluded.archived_at,archived_by=excluded.archived_by,
                last_activity=excluded.last_activity,through_seq=excluded.through_seq,
                restored_at=excluded.restored_at,restored_by=excluded.restored_by''',
                (room,revision,None if restore else stamp,None if restore else actor,
                 item['updated'],item['latest_seq'],stamp if restore else None,actor if restore else None))
            return {'room':room,'archived':not restore,'archive_version':revision,
                    'last_activity':item['updated'],'through_seq':item['latest_seq'],
                    'backend_work':'not_required' if restore else 'clear',
                    'external_work':'not_required' if restore else 'clear',
                    'external_proof':None if restore else VERIFIED_EXTERNAL.get()}
        return self.store.mutate(actor,key,{'op':'room_restore' if restore else 'room_archive',
            'room':room,'payload':payload},action,room)
