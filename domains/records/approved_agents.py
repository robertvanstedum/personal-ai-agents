"""Owner-approved Rooms access. Secrets travel only to the fixed local installer.

Records is the authority. No host paths or plaintext credentials are stored here.
Unmanaged installations remain explicitly legacy until the owner adopts them.
"""
import hashlib
import json
import secrets
from uuid import uuid4
from domains.records.platform_access import PlatformAccess, AccessError, hashed, stamp

AGENTS = {'cos': 'Chief of Staff', 'mc': 'Master Craftsman', 'claude-code': 'Claude Code', 'codex': 'Codex'}
OPERATIONS = ['read', 'post', 'receipt', 'upload', 'link', 'rsvp']


def approval(db, agent):
    return db.execute('SELECT * FROM agent_approvals WHERE agent=?', (agent,)).fetchone()


def allowed(db, agent):
    row = approval(db, agent)
    return row is None or row['state'] == 'active'  # unchanged legacy; never labelled approved


def turn_allowed(db, turn):
    row = approval(db, turn['addressee'])
    return row is None or (row['state'] == 'active' and turn['approval_generation'] == row['generation'])


class ApprovedAgents:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS agent_approvals(
                agent TEXT PRIMARY KEY, generation TEXT NOT NULL, state TEXT NOT NULL,
                changed_by TEXT NOT NULL, changed_at TEXT NOT NULL, installed_at TEXT,
                brief TEXT NOT NULL, brief_hash TEXT NOT NULL, member_id TEXT, worker_id TEXT);
            CREATE TABLE IF NOT EXISTS agent_credential_generations(
                credential TEXT PRIMARY KEY, agent TEXT NOT NULL, generation TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS agent_approval_audit(
                id TEXT PRIMARY KEY, agent TEXT NOT NULL, generation TEXT NOT NULL,
                action TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL);
            ''')
            if 'approval_generation' not in {r['name'] for r in db.execute('PRAGMA table_info(turns)')}:
                db.execute('ALTER TABLE turns ADD COLUMN approval_generation TEXT')

    @staticmethod
    def validate(agent):
        if agent not in AGENTS:
            raise AccessError('Choose CoS, MC, Claude Code or Codex', 422)

    @staticmethod
    def audit(db, agent, generation, action, actor):
        db.execute('INSERT INTO agent_approval_audit VALUES(?,?,?,?,?,?)',
                   (str(uuid4()), agent, generation, action, actor, stamp()))

    def owner(self, db, actor):
        self.store.owner(actor)
        PlatformAccess.enforce(db, actor, None, 'admin')

    def inventory(self, actor):
        with self.store.connect() as db:
            self.owner(db, actor)
            rows = []
            for agent, label in AGENTS.items():
                r = approval(db, agent)
                row = dict(r) if r else dict(agent=agent, state='legacy', generation=None, brief='', brief_hash=None)
                row.pop('member_id', None); row.pop('worker_id', None)
                busy = db.execute("SELECT 1 FROM turns WHERE addressee=? AND state IN ('claimed','running')", (agent,)).fetchone()
                connected = db.execute("SELECT observed_at FROM presence WHERE actor=? AND kind='reachable' AND expires_at>? ORDER BY observed_at DESC LIMIT 1", (agent, stamp())).fetchone()
                row.update(label=label, approved=bool(r and r['state']=='active'),
                           availability='Busy' if busy else 'Connected' if connected else 'Unknown',
                           observed_at=connected['observed_at'] if connected else None,
                           repo_write={'state':'unavailable','paths':[]}, host_write={'state':'unavailable','paths':[]},
                           attachment_delivery='metadata_only', image_understanding=False)
                rows.append(row)
            return rows

    def revoke_all(self, db, agent):
        self.revoke_identity(db, agent)
        self.revoke_identity(db, agent+'-manual')

    def revoke_identity(self, db, agent):
        # Revoke membership + every hosted worker installation, including legacy keys.
        workers = [r['installation_id'] for r in db.execute('SELECT installation_id FROM hosted_teammates WHERE principal=?', (agent,))]
        for worker in workers:
            if db.execute('SELECT 1 FROM hosted_teammates WHERE installation_id=? AND principal<>?', (worker,agent)).fetchone():
                raise AccessError('Separate the shared worker installation before changing this approval',409)
        ids = {r['id'] for r in db.execute('SELECT id FROM client_installations WHERE principal=?', (agent,))} | set(workers)
        for installation in ids:
            for r in db.execute('SELECT id FROM client_credentials WHERE installation=? AND revoked IS NULL', (installation,)).fetchall():
                db.execute('UPDATE client_credentials SET revoked=? WHERE id=?', (stamp(), r['id']))
                db.execute('INSERT INTO credential_audit VALUES(?,?,?,?,?)',(str(uuid4()), 'robert', r['id'], 'approval_revoke',stamp()))
                # Ends web sessions as well as Records turn admission.
                if self.store.platform_access.on_revoke:
                    import inspect
                    hook=self.store.platform_access.on_revoke
                    if len(inspect.signature(hook).parameters)>=3: hook(db,agent,r['id'])
                    else: hook(db,agent)
        self.store.meetings.fence_principal(db, agent, 'approval_withdrawn')
        db.execute("UPDATE turns SET state='cancelled',disposition='approval_withdrawn',updated=? WHERE addressee=? AND state NOT IN ('committed','cancelled','superseded','expired','failed','abandoned')", (stamp(), agent))
        db.execute("DELETE FROM presence WHERE actor=? AND kind IN ('reachable','answering')", (agent,))

    def change(self, actor, agent, enabled, brief=None):
        self.validate(agent)
        if type(enabled) is not bool: raise AccessError('enabled must be a boolean',422)
        if brief is not None and (not isinstance(brief,str) or len(brief)>8000):
            raise AccessError('Brief must contain at most 8000 characters',422)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE'); self.owner(db,actor)
            old=approval(db,agent)
            if old and ((enabled and old['state'] in ('pending','active')) or (not enabled and old['state']=='off')):
                if brief is not None and brief != old['brief']:
                    raise AccessError('Switch off before replacing the standing brief',409)
                return {'agent':agent,'state':old['state'],'generation':old['generation']}
            kind=db.execute('SELECT kind FROM principals WHERE id=?',(agent,)).fetchone()
            if not kind or kind['kind']!='agent': raise AccessError('Provision the existing agent identity first',409)
            self.revoke_all(db,agent)
            generation=str(uuid4()); text=brief if brief is not None else old['brief'] if old else ''
            digest=hashlib.sha256(text.encode()).hexdigest()
            state='pending' if enabled else 'off'
            db.execute('INSERT OR REPLACE INTO agent_approvals VALUES(?,?,?,?,?,NULL,?,?,NULL,NULL)',(agent,generation,state,actor,stamp(),text,digest))
            self.audit(db,agent,generation,'approve_requested' if enabled else 'off',actor)
            return {'agent':agent,'state':state,'generation':generation}

    def edit_brief(self, actor, agent, text):
        self.validate(agent)
        if not isinstance(text,str) or len(text)>8000:
            raise AccessError('Brief must contain at most 8000 characters',422)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE'); self.owner(db,actor)
            row=approval(db,agent)
            if not row: raise AccessError('Adopt this agent before editing its brief',409)
            if row['state']=='pending': raise AccessError('Wait for installation before editing the brief',409)
            digest=hashlib.sha256(text.encode()).hexdigest()
            db.execute('UPDATE agent_approvals SET brief=?,brief_hash=? WHERE agent=?',(text,digest,agent))
            self.audit(db,agent,row['generation'],'brief:'+digest,actor)
            return {'agent':agent,'brief_hash':digest,'generation':row['generation']}

    def pending(self):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute("SELECT agent,generation,brief_hash FROM agent_approvals WHERE state='pending'")]

    def issue(self, agent, generation):
        """Installer only. A lost reply is retried by rotating the unactivated pair.

        Only hashes are stored, so an earlier secret can never be fetched again.
        Pending credentials cannot authenticate until installation is acknowledged.
        """
        self.validate(agent)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=approval(db,agent)
            if not row or row['state']!='pending' or row['generation']!=generation:
                raise AccessError('Setup generation is no longer pending',409)
            self.revoke_all(db,agent)
            work_principal='approved-worker-'+agent
            db.execute('INSERT OR IGNORE INTO principals VALUES(?,?,?,?,?)',(work_principal,work_principal,'service',hashed(secrets.token_urlsafe(40)),stamp()))
            pair={}
            for scope,principal,operations in [('membership',agent,OPERATIONS),('work',work_principal,['work'])]:
                installation='approved:'+agent+':'+scope
                db.execute('INSERT OR IGNORE INTO client_installations VALUES(?,?,?,?)',(installation,principal,'Approved '+agent+' '+scope,stamp()))
                if scope=='work':
                    db.execute('INSERT OR IGNORE INTO hosted_teammates VALUES(?,?,?)',(installation,agent,stamp()))
                token=secrets.token_urlsafe(36); cid=str(uuid4())
                db.execute('INSERT INTO client_credentials VALUES(?,?,?,?,NULL,NULL,NULL,0)',(cid,installation,hashed(token),stamp()))
                db.execute('INSERT INTO credential_scopes VALUES(?,?,?)',(cid,scope,json.dumps(operations)))
                db.execute('INSERT INTO agent_credential_generations VALUES(?,?,?)',(cid,agent,generation))
                pair[scope]={'credential_id':cid,'token':token}
            db.execute('UPDATE agent_approvals SET member_id=?,worker_id=? WHERE agent=?',(pair['membership']['credential_id'],pair['work']['credential_id'],agent))
            self.audit(db,agent,generation,'issued','local-installer')
            return {'agent':agent,'generation':generation,'credentials':pair,'brief':row['brief'],'brief_hash':row['brief_hash']}

    def acknowledge(self, agent, payload):
        self.validate(agent)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE'); row=approval(db,agent)
            if not row or row['state'] not in ('pending','active') or any(row[k]!=payload.get(k) for k in ('generation','member_id','worker_id','brief_hash')) or not row['member_id']:
                raise AccessError('Stale installation acknowledgement',409)
            if row['state']=='pending':
                db.execute("UPDATE agent_approvals SET state='active',installed_at=? WHERE agent=?",(stamp(),agent))
                self.audit(db,agent,row['generation'],'installed','local-installer')
            return {'agent':agent,'state':'active','generation':row['generation']}
