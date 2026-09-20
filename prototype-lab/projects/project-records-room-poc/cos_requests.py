"""Owner-requested CoS jobs. No inference in HTTP handlers or implicit retries."""
import json
from datetime import datetime, timezone, timedelta
from uuid import UUID, uuid4
from store import Problem, canonical, now


class CoSRequests:
    def __init__(self, store, allowed_sessions=()):
        self.store=store
        self.allowed=frozenset(allowed_sessions)
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS cos_requests(
                id TEXT PRIMARY KEY,room TEXT NOT NULL,actor TEXT NOT NULL,
                action TEXT NOT NULL,guard TEXT NOT NULL,state TEXT NOT NULL,
                created TEXT NOT NULL,expires TEXT NOT NULL,result TEXT)''')

        self._auto_schema()

    def _auto_schema(self):
        with self.store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cos_auto(
                    room TEXT PRIMARY KEY,enabled INTEGER NOT NULL,generation TEXT NOT NULL,
                    cursor INTEGER NOT NULL,remaining INTEGER NOT NULL,expires TEXT NOT NULL,reason TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cos_auto_audit(
                    id TEXT PRIMARY KEY,room TEXT NOT NULL,actor TEXT NOT NULL,
                    enabled INTEGER NOT NULL,at TEXT NOT NULL,generation TEXT);
                CREATE TABLE IF NOT EXISTS cos_auto_jobs(
                    request_id TEXT PRIMARY KEY,room TEXT NOT NULL,generation TEXT NOT NULL,
                    previous_cursor INTEGER NOT NULL,trigger_seq INTEGER NOT NULL);
            """)

    def auto_status(self, db, room):
        row=db.execute('SELECT * FROM cos_auto WHERE room=?',(room,)).fetchone()
        result = dict(row) if row else {'enabled':False,'remaining':0,'reason':'not_invited','expires':None}
        result['expired'] = bool(result['enabled'] and result['expires'] <= now())
        result['active'] = bool(result['enabled'] and not result['expired'])
        return result

    def set_auto(self, actor, room, payload):
        self.authorized(actor,room)
        if room not in self.allowed:raise Problem('CoS is not enabled for this test session',403)
        if set(payload)!={'enabled'} or type(payload['enabled']) is not bool:raise Problem('Choose enabled true or false')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=self.store.access(db,actor,room)
            existing=db.execute('SELECT * FROM cos_auto WHERE room=?',(room,)).fetchone()
            if payload['enabled']:
                if current['state']!='active':raise Problem('Session is not active',409)
                member=db.execute('SELECT role FROM members WHERE room=? AND actor=?',(room,'cos-dev')).fetchone()
                if not member or member['role']!='contributor':raise Problem('Invite CoS as contributor first',409)
                if existing and existing['enabled'] and existing['expires']>now() and existing['remaining']>0:
                    return self.auto_status(db,room) # lost-response retry never replenishes budget
                cursor=db.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE room=? AND actor='cos-dev'",(room,)).fetchone()[0]
                if existing:cursor=max(cursor,existing['cursor'])
                db.execute('INSERT OR REPLACE INTO cos_auto VALUES(?,?,?,?,?,?,?)',
                    (room,1,str(uuid4()),cursor,20,(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),'active'))
            elif existing:
                pending=db.execute("SELECT MIN(j.previous_cursor) FROM cos_auto_jobs j JOIN cos_requests r ON r.id=j.request_id WHERE j.room=? AND j.generation=? AND r.state IN ('queued','running')",(room,existing['generation'])).fetchone()[0]
                if pending is not None:db.execute('UPDATE cos_auto SET cursor=MIN(cursor,?) WHERE room=?',(pending,room))
                db.execute("UPDATE cos_auto SET enabled=0,reason='paused_by_owner' WHERE room=?",(room,))
                db.execute("UPDATE cos_requests SET state='cancelled' WHERE state='queued' AND id IN (SELECT request_id FROM cos_auto_jobs WHERE room=?)",(room,))
            updated=self.auto_status(db,room)
            db.execute('INSERT INTO cos_auto_audit VALUES(?,?,?,?,?,?)',(str(uuid4()),room,actor,int(payload['enabled']),now(),updated.get('generation')))
            return updated

    def check_auto_authority(self, request_id):
        with self.store.connect() as db:
            job=db.execute('SELECT * FROM cos_auto_jobs WHERE request_id=?',(request_id,)).fetchone()
            if job is None:return # manual explicit request
            mode=db.execute('SELECT * FROM cos_auto WHERE room=?',(job['room'],)).fetchone()
            room=db.execute('SELECT state FROM rooms WHERE id=?',(job['room'],)).fetchone()
            member=db.execute('SELECT role FROM members WHERE room=? AND actor=?',(job['room'],'cos-dev')).fetchone()
            if not mode or not mode['enabled'] or mode['expires']<=now() or mode['generation']!=job['generation'] or not room or room['state']!='active' or not member or member['role']!='contributor':
                raise Problem('Automatic participation ended',403)

    def schedule_auto(self):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for mode in db.execute('SELECT * FROM cos_auto WHERE enabled=1').fetchall():
                room=mode['room'];current=db.execute('SELECT * FROM rooms WHERE id=?',(room,)).fetchone()
                member=db.execute('SELECT role FROM members WHERE room=? AND actor=?',(room,'cos-dev')).fetchone()
                reason=None
                if room not in self.allowed or not current or current['state']!='active' or not member or member['role']!='contributor':reason='access_or_session_ended'
                elif mode['expires']<=now():reason='expired'
                elif mode['remaining']<=0:reason='budget_exhausted'
                # Do not disable on exhausted budget while its last queued turn is running.
                pending=db.execute("SELECT 1 FROM cos_requests WHERE room=? AND state IN ('queued','queued_reconcile','running','uncertain')",(room,)).fetchone()
                if reason and not (reason=='budget_exhausted' and pending):
                    db.execute('UPDATE cos_auto SET enabled=0,reason=? WHERE room=?',(reason,room));continue
                if pending or reason:continue
                event=db.execute("SELECT seq FROM events WHERE room=? AND actor='robert' AND kind='message' AND origin IS NULL AND seq>? ORDER BY seq DESC LIMIT 1",(room,mode['cursor'])).fetchone()
                if not event:continue
                seq=db.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE room=?',(room,)).fetchone()[0]
                key=str(uuid4());guard=canonical({'version':current['version'],'last_seq':seq})
                db.execute('INSERT INTO cos_requests VALUES(?,?,?,?,?,?,?,?,NULL)',(key,room,'robert','contribute',guard,'queued',now(),min(mode['expires'],(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat())))
                db.execute('INSERT INTO cos_auto_jobs VALUES(?,?,?,?,?)',(key,room,mode['generation'],mode['cursor'],event['seq']))
                db.execute('UPDATE cos_auto SET cursor=?,remaining=remaining-1 WHERE room=?',(event['seq'],room))

    def authorized(self, actor, room):
        self.store.owner(actor)
        with self.store.connect() as db:self.store.access(db,actor,room)

    def submit(self, actor, room, request_id, payload):
        self.authorized(actor,room)
        if room not in self.allowed:raise Problem('CoS is not enabled for this non-private test session',403)
        if set(payload)!={'action'} or payload['action'] not in {'contribute','brief'}:
            raise Problem('Choose contribute or brief')
        try:
            if str(UUID(request_id))!=request_id:raise ValueError
        except (ValueError,TypeError,AttributeError):raise Problem('Retain a canonical request UUID')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=self.store.access(db,actor,room)
            old=db.execute('SELECT * FROM cos_requests WHERE id=?',(request_id,)).fetchone()
            if old:
                if old['room']!=room or old['action']!=payload['action'] or old['actor']!=actor:raise Problem('Request ID already used for another request',409)
                return self.public(old)
            if current['state']!='active':raise Problem('Session is not active',409)
            member=db.execute('SELECT role FROM members WHERE room=? AND actor=?',(room,'cos-dev')).fetchone()
            if not member or member['role']!='contributor':raise Problem('Invite CoS as a contributor first',409)
            if db.execute("SELECT 1 FROM cos_requests WHERE room=? AND state IN ('queued','queued_reconcile','running','uncertain')",(room,)).fetchone():
                raise Problem('An existing CoS request needs completion or reconciliation',409)
            seq=db.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE room=?',(room,)).fetchone()[0]
            guard=canonical({'version':current['version'],'last_seq':seq})
            expires=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()
            db.execute('INSERT INTO cos_requests VALUES(?,?,?,?,?,?,?,?,NULL)',(request_id,room,actor,payload['action'],guard,'queued',now(),expires))
            return self.public(db.execute('SELECT * FROM cos_requests WHERE id=?',(request_id,)).fetchone())

    @staticmethod
    def public(row):
        return {k:row[k] for k in ('id','room','action','state','created','expires')} | {'result':json.loads(row['result']) if row['result'] else None}

    def status(self, actor, room):
        self.authorized(actor,room)
        with self.store.connect() as db:
            rows=db.execute('SELECT * FROM cos_requests WHERE room=? ORDER BY created DESC LIMIT 10',(room,)).fetchall()
        with self.store.connect() as db:auto=self.auto_status(db,room)
        return {'enabled':room in self.allowed,'scope':'non_private_test_only','auto':auto,'requests':[self.public(r) for r in rows]}

    def reconcile(self, actor, room, request_id):
        self.authorized(actor,room)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM cos_requests WHERE id=? AND room=?',(request_id,room)).fetchone()
            if not row:raise Problem('Request not found',404)
            if row['state']=='uncertain':
                db.execute("UPDATE cos_requests SET state='queued_reconcile' WHERE id=?",(request_id,))
            return self.public(db.execute('SELECT * FROM cos_requests WHERE id=?',(request_id,)).fetchone())

    def recover_interrupted(self):
        # Called by the single worker at startup, never by a request handler.
        with self.store.connect() as db:db.execute("UPDATE cos_requests SET state='uncertain' WHERE state='running'")

    def run_once(self, responder, reconciler=None):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM cos_requests WHERE state IN ('queued','queued_reconcile') ORDER BY created LIMIT 1").fetchone()
            if row is None:return False
            row=dict(row)
            db.execute("UPDATE cos_requests SET state='running' WHERE id=?",(row['id'],))
        state='uncertain';result=None
        try:
            room=self.store.room('robert',row['room'])
            if row['state']=='queued_reconcile':
                if reconciler is None:raise Problem('Reconciliation unavailable')
                result=reconciler(row['room'],row['id'],row['action'])
                state='committed'
            elif row['room'] not in self.allowed or row['expires']<=now() or room['state']!='active' or room['contribution_guard']!=json.loads(row['guard']):
                state='cancelled'
            else:
                self.check_auto_authority(row['id'])
                result=responder(row['room'],row['id'],row['action'],json.loads(row['guard']))
                state='committed'
        except Problem:
            state='cancelled'
        except Exception as error:
            # Connector conflict is a confirmed rejection, not uncertain delivery.
            # A later owner message can get a fresh response without rerunning this one.
            if getattr(error,'definitive_conflict',False) is True:state='superseded'
            else:state='uncertain'
            # Upstream exceptions can contain secrets. Keep them off UI/logs.
        with self.store.connect() as db:
            auto_job=db.execute('SELECT * FROM cos_auto_jobs WHERE request_id=?',(row['id'],)).fetchone()
            if result and auto_job:
                result['operation'].update(trigger_mode='room_auto_reply',background_listener=True,
                                           trigger_record_seq=auto_job['trigger_seq'])
            db.execute('UPDATE cos_requests SET state=?,result=? WHERE id=?',(state,canonical(result) if result else None,row['id']))
        if state=='cancelled':
            with self.store.connect() as db:
                job=db.execute('SELECT * FROM cos_auto_jobs WHERE request_id=?',(row['id'],)).fetchone()
                if job:
                    db.execute('UPDATE cos_auto SET cursor=MIN(cursor,?) WHERE room=? AND generation=? AND enabled=1',
                               (job['previous_cursor'],job['room'],job['generation']))
        return True
