"""Owner-requested CoS jobs. No inference in HTTP handlers or implicit retries."""
import json
from datetime import datetime, timezone, timedelta
from uuid import UUID
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
        return {'enabled':room in self.allowed,'scope':'non_private_test_only','requests':[self.public(r) for r in rows]}

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
                result=responder(row['room'],row['id'],row['action'],json.loads(row['guard']))
                state='committed'
        except Exception:
            # Upstream exceptions can contain secrets. Keep them off UI/logs.
            state='uncertain'
        with self.store.connect() as db:db.execute('UPDATE cos_requests SET state=?,result=? WHERE id=?',(state,canonical(result) if result else None,row['id']))
        return True
