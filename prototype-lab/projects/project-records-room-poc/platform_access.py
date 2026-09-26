"""Platform client credentials; room membership remains owned by Records.

Uses the local application's transaction boundary, not a separate network service.
No credential values are persisted or returned in idempotency receipts.
"""
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import secrets
from uuid import uuid4

request_credential = ContextVar('request_credential', default=None)
request_operation = ContextVar('request_operation', default='read')


class AccessError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def stamp():
    return datetime.now(timezone.utc).isoformat()


def hashed(token):
    return hashlib.sha256(token.encode()).hexdigest()


class PlatformAccess:
    def __init__(self, connect):
        self.connect = connect
        with connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS client_installations(
                    id TEXT PRIMARY KEY,principal TEXT NOT NULL REFERENCES principals(id),
                    label TEXT NOT NULL,created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS client_credentials(
                    id TEXT PRIMARY KEY,installation TEXT NOT NULL REFERENCES client_installations(id),
                    token_hash TEXT UNIQUE NOT NULL,created TEXT NOT NULL,expires TEXT,
                    revoked TEXT,grants TEXT,legacy INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS credential_audit(
                    id TEXT PRIMARY KEY,actor TEXT NOT NULL,credential TEXT NOT NULL,
                    action TEXT NOT NULL,created TEXT NOT NULL);
            ''')
            # One-time compatible import. Existing keys are not silently expired.
            # Revocation never falls back to principals.token_hash.
            for row in db.execute('SELECT id,label,token_hash,created FROM principals').fetchall():
                self.import_legacy(db, row)

    @staticmethod
    def import_legacy(db, row):
        installation = 'legacy:' + row['id']
        db.execute('INSERT OR IGNORE INTO client_installations VALUES(?,?,?,?)',
                   (installation, row['id'], 'Legacy ' + row['label'], row['created']))
        db.execute('INSERT OR IGNORE INTO client_credentials VALUES(?,?,?,?,?,?,?,?)',
                   (installation, installation, row['token_hash'], row['created'], None, None, None, 1))

    @staticmethod
    def lookup(db, credential_id=None, token=None):
        if token is not None:
            if not isinstance(token, str) or not token:
                return None
            where, value = 'c.token_hash=?', hashed(token)
        else:
            where, value = 'c.id=?', credential_id
        row = db.execute('''SELECT p.id,p.label,p.kind,c.id AS credential_id,
            c.installation AS installation_id,c.grants,c.legacy,c.expires,c.revoked
            FROM client_credentials c JOIN client_installations i ON i.id=c.installation
            JOIN principals p ON p.id=i.principal WHERE ''' + where, (value,)).fetchone()
        if not row or row['revoked'] or (row['expires'] and row['expires'] <= stamp()):
            return None
        return dict(row)

    def authenticate(self, token=None, credential_id=None):
        with self.connect() as db:
            return self.lookup(db, credential_id, token)

    @staticmethod
    def enforce(db, principal, destination, operation):
        cid = request_credential.get()
        if cid is None:  # trusted in-process callers; HTTP always installs context
            return
        auth = PlatformAccess.lookup(db, credential_id=cid)
        if not auth or auth['id'] != principal:
            raise AccessError('Credential expired or revoked', 401)
        if auth['legacy']:
            return
        grants = json.loads(auth['grants'])
        if operation not in grants.get(destination, []):
            raise AccessError('Credential does not grant this destination operation', 403)

    @staticmethod
    def validate_grants(db, principal, grants):
        allowed = {'read', 'post', 'upload', 'link', 'receipt', 'export'}
        if not isinstance(grants, dict) or not grants or len(grants) > 32:
            raise AccessError('Provide 1–32 explicit session grants')
        for room, operations in grants.items():
            if not isinstance(operations, list) or not operations or any(not isinstance(v,str) or v not in allowed for v in operations):
                raise AccessError('Unsupported credential operation')
            if not db.execute('SELECT 1 FROM members WHERE room=? AND actor=?', (room, principal)).fetchone():
                raise AccessError('Grant requires existing session membership')
        return json.dumps(grants, sort_keys=True)

    def issue(self, owner, payload):
        if owner != 'robert':
            raise AccessError('Only Robert manages client credentials', 403)
        principal, label = payload.get('principal'), payload.get('label')
        if not isinstance(principal,str) or principal == 'robert':
            raise AccessError('Choose a collaborator principal')
        if not isinstance(label,str) or not 1 <= len(label.strip()) <= 160:
            raise AccessError('Provide an installation label')
        try:
            expiry = datetime.fromisoformat(payload.get('expires_at',''))
            if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc): raise ValueError()
        except (TypeError,ValueError):
            raise AccessError('expires_at must be a future timezone-aware date')
        expiry = expiry.astimezone(timezone.utc).isoformat()
        token, cid = secrets.token_urlsafe(36), str(uuid4())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.enforce(db,owner,None,'admin')
            grants = self.validate_grants(db,principal,payload.get('grants'))
            installation = payload.get('installation_id')
            if installation:
                if not isinstance(installation,str): raise AccessError("Invalid installation ID")
                row=db.execute('SELECT principal FROM client_installations WHERE id=?',(installation,)).fetchone()
                if not row or row['principal']!=principal or installation.startswith('legacy:'):
                    raise AccessError('Installation does not belong to this collaborator')
            else:
                installation=str(uuid4())
                db.execute('INSERT INTO client_installations VALUES(?,?,?,?)',(installation,principal,label,stamp()))
            old = payload.get('rotate_credential_id')
            if old:
                row=db.execute('SELECT installation FROM client_credentials WHERE id=?',(old,)).fetchone()
                if not row or row['installation']!=installation:
                    raise AccessError('Rotation requires a credential of this installation')
                db.execute('UPDATE client_credentials SET revoked=? WHERE id=?',(stamp(),old))
            db.execute('INSERT INTO client_credentials VALUES(?,?,?,?,?,?,?,0)',
                       (cid,installation,hashed(token),stamp(),expiry,None,grants))
            db.execute('INSERT INTO credential_audit VALUES(?,?,?,?,?)',(str(uuid4()),owner,cid,'rotate' if old else 'issue',stamp()))
        return dict(credential_id=cid,installation_id=installation,principal=principal,
                    expires_at=expiry,access_token=token)

    def inventory(self, owner):
        if owner!='robert': raise AccessError('Only Robert manages client credentials',403)
        with self.connect() as db:
            self.enforce(db,owner,None,'admin')
            return [dict(row) for row in db.execute('''SELECT c.id AS credential_id,
                c.installation AS installation_id,i.principal,i.label,c.created,c.expires,
                c.revoked,c.legacy,c.grants FROM client_credentials c JOIN client_installations i
                ON c.installation=i.id ORDER BY c.created,c.id''')]

    def revoke(self, owner, cid):
        if owner!='robert': raise AccessError('Only Robert manages client credentials',403)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.enforce(db,owner,None,'admin')
            row=db.execute('''SELECT i.principal FROM client_credentials c
                JOIN client_installations i ON c.installation=i.id WHERE c.id=?''',(cid,)).fetchone()
            if not row: raise AccessError('Credential not found',404)
            if row['principal']=='robert': raise AccessError('Owner recovery key is not a collaborator credential')
            db.execute('UPDATE client_credentials SET revoked=COALESCE(revoked,?) WHERE id=?',(stamp(),cid))
            db.execute('INSERT INTO credential_audit VALUES(?,?,?,?,?)',(str(uuid4()),owner,cid,'revoke',stamp()))
        return dict(credential_id=cid,status='revoked')
