"""One-shot EMPTY Records state preparation. No key generation, network or activation.

Inputs are private file references plus exact raw-file SHA256 pins. Independent,
fresh production-only key custody remains the operator's separate responsibility.
The explicit runtime_data_dir MUST equal the later container's --data-dir.
"""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from services import records_runtime as runtime

FIELDS = {'destination', 'runtime_data_dir', 'owner_key_file', 'session_key_file', 'portal_key_file', 'key_sha256'}
KEYS = ('owner', 'session', 'portal')

def need(ok, code):
    if not ok:
        raise ValueError(code)

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def absolute(value):
    need(isinstance(value, str) and value.startswith('/') and str(Path(value)) == value and '..' not in Path(value).parts and not any(c in value for c in '\n\r\x00'), 'canonical_absolute_path_required')
    return Path(value)

def directory(path, private=False):
    """Anchor every component; allow sticky /private/tmp only as shared ancestor."""
    path = absolute(str(path))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        current = Path('/')
        for component in path.parts[1:]:
            other = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = other
            current /= component
            s = os.fstat(fd)
            sticky_tmp = current in {Path('/tmp'), Path('/private/tmp')} and bool(s.st_mode & stat.S_ISVTX) and s.st_uid == 0
            need(s.st_uid in {0, os.geteuid()} and (not s.st_mode & 0o022 or sticky_tmp), 'untrusted_directory')
        if private:
            need(not os.fstat(fd).st_mode & 0o077, 'private_parent_required')
        return fd
    except BaseException:
        os.close(fd)
        raise

def read_private(path):
    path = absolute(path)
    parent = directory(path.parent, private=True)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            before = os.fstat(fd)
            need(stat.S_ISREG(before.st_mode) and before.st_uid in {0, os.geteuid()} and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1 and 0 < before.st_size <= 4096, 'private_small_input_required')
            raw = os.read(fd, 4097)
            after = os.fstat(fd)
            named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode, s.st_uid, s.st_gid, s.st_nlink)
            need(identity(before) == identity(after) == identity(named) and len(raw) == before.st_size, 'input_changed')
            return raw
        finally:
            os.close(fd)
    finally:
        os.close(parent)

def write_file(root, name, raw):
    fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())

def json_file(root, name, body):
    write_file(root, name, json.dumps(body, sort_keys=True, separators=(',', ':')).encode() + b'\n')

def sync_directory(path):
    fd = directory(path, private=True)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def store_type():
    app = runtime.APP_DIR
    # Existing Store uses local module imports. This is a one-shot operator process,
    # not Flask startup and not a second schema implementation.
    need(app.is_dir(), 'reviewed_records_source_required')
    sys.path.insert(0, str(app))
    spec = importlib.util.spec_from_file_location('records_empty_preparer_store', app / 'store.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def initialize(root):
        store = module.Store(root)
        # Same non-request initializers as create_app; never load CoS config or
        # construct Flask, call an agent, issue a credential or admit a session.
        from web_sessions import WebSessions
        import portal_identity
        from cos_requests import CoSRequests
        from coordination import Coordination
        WebSessions(store.connect)
        with store.connect() as db:
            db.executescript(portal_identity.SCHEMA)
        CoSRequests(store, ())
        Coordination(store)
        return store
    return initialize

def verify_empty(db, owner_hash):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    need(runtime.REQUIRED_TABLES <= tables, 'schema_incomplete')
    need(dict(db.execute('SELECT key,value FROM meta')) == {'schema_version': '5', 'rooms_r1_schema': '1'}, 'schema_versions_wrong')
    need(db.execute('SELECT id,label,kind,token_hash FROM principals').fetchall() == [('robert', 'Robert', 'human', owner_hash)], 'canonical_owner_wrong')
    need(db.execute('SELECT id,principal,label FROM client_installations').fetchall() == [('legacy:robert', 'robert', 'Legacy Robert')], 'legacy_installation_wrong')
    need(db.execute('SELECT id,installation,token_hash,expires,revoked,grants,legacy FROM client_credentials').fetchall() == [('legacy:robert', 'legacy:robert', owner_hash, None, None, None, 1)], 'legacy_credential_wrong')
    for table in tables - {'meta', 'principals', 'client_installations', 'client_credentials'}:
        need(db.execute('SELECT COUNT(*) FROM "' + table.replace('"', '""') + '"').fetchone()[0] == 0, 'unexpected_content_or_grant')

def prepare(inputs):
    need(isinstance(inputs, dict) and set(inputs) == FIELDS, 'exact_input_fields_required')
    root = absolute(inputs['destination'])
    target = absolute(inputs['runtime_data_dir'])
    need(target != Path('/'), 'runtime_root_required')
    pins = inputs['key_sha256']
    need(isinstance(pins, dict) and set(pins) == set(KEYS) and all(isinstance(v, str) and re.fullmatch('[a-f0-9]{64}', v) for v in pins.values()), 'exact_key_pins_required')
    paths = {key: absolute(inputs[key + '_key_file']) for key in KEYS}
    need(len(set(paths.values())) == 3 and all(root not in p.parents and root != p for p in paths.values()), 'separate_key_inputs_required')
    raw = {k: read_private(str(p)) for k, p in paths.items()}
    need(all(digest(raw[k]) == pins[k] for k in KEYS), 'key_input_pin_changed')
    values = {k: raw[k].strip() for k in KEYS}
    for key in ('owner', 'session'):
        need(bool(re.fullmatch(rb'[A-Za-z0-9_-]{32,256}', values[key])), 'store_key_format_wrong')
    try:
        kid, sep, secret = values['portal'].decode('ascii').partition(':')
    except UnicodeError:
        raise ValueError('portal_key_format_wrong') from None
    need(sep and re.fullmatch('[A-Za-z0-9_-]{1,64}', kid) and re.fullmatch('[A-Za-z0-9_-]{32,256}', secret), 'portal_key_format_wrong')
    need(len({values['owner'], values['session'], secret.encode()}) == 3, 'independent_keys_required')
    parent = directory(root.parent, private=True)
    try:
        space = os.fstatvfs(parent)
        need(space.f_bavail * space.f_frsize >= (5 << 30) + (16 << 20), 'disk_reserve_refused')
        os.mkdir(root.name, 0o700, dir_fd=parent)  # Existing roots ALWAYS refuse.
        os.fsync(parent)
    finally:
        os.close(parent)
    json_file(root, 'INCOMPLETE', {'format': 'records-empty-preparation/1', 'automatic_retry': False})
    sync_directory(root)
    # Failure deliberately leaves the entire INCOMPLETE attempt, never deletes it.
    write_file(root, 'owner-key.txt', values['owner'] + b'\n')
    write_file(root, 'session-key.txt', values['session'] + b'\n')
    store = store_type()(root)
    need(store.owner_key.encode() == values['owner'] and store.session_key.encode() == values['session'], 'store_changed_keys')
    with closing(sqlite3.connect(root / 'records.sqlite3')) as db:
        verify_empty(db, digest(values['owner']))
        need(db.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'integrity_check_failed')
        need(db.execute('PRAGMA foreign_key_check').fetchall() == [], 'foreign_key_check_failed')
        need(db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0, 'wal_checkpoint_busy')
        schema = runtime.schema_hash(db)
    # No copied SQLite sidecar is needed: all connections closed/checkpointed.
    for name in ('records.sqlite3-wal', 'records.sqlite3-shm'):
        need(not (root / name).exists(), 'sqlite_sidecar_still_present')
    marker = {'format': 'records-production-runtime/1', 'environment': 'production', 'data_dir': str(target), 'schema_sha256': schema, 'owner_key_sha256': digest(values['owner']), 'session_key_sha256': digest(values['session']), 'portal_key_kid': kid}
    # Re-read every explicit input before publishing completion.
    need(all(read_private(str(paths[k])) == raw[k] for k in KEYS), 'key_input_changed_during_prepare')
    json_file(root, runtime.MANIFEST, marker)
    receipt = {'format': 'records-empty-preparation/1', 'runtime_data_dir': str(target), 'schema_sha256': schema, 'key_input_sha256': pins, 'canonical_principal': 'robert', 'canonical_credential': 'legacy:robert', 'human_login_created': False, 'content_rows': 0, 'agent_grants': 0, 'key_generation': False, 'provider_calls': 0, 'activated': False, 'key_freshness_verified': False, 'key_freshness_required_before_activation': True}
    for p in root.iterdir():
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            need(stat.S_ISREG(os.fstat(fd).st_mode), 'unexpected_output_type')
            os.fsync(fd)
        finally:
            os.close(fd)
    json_file(root, 'COMPLETE', receipt)
    sync_directory(root)
    (root / 'INCOMPLETE').unlink()
    sync_directory(root)
    return {'prepared': True, 'activated': False, 'content_rows': 0, 'agent_grants': 0, 'provider_calls': 0, 'schema_sha256': schema}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'plan_only': True, 'writes': 0, 'key_reads': 0, 'key_generation': False, 'activated': False}))
        return
    need(os.geteuid() == 0, 'root_operator_required')
    need(args.inputs is not None, 'private_inputs_required')
    print(json.dumps(prepare(json.loads(read_private(args.inputs))), sort_keys=True))

if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, sqlite3.Error):
        print(json.dumps({'prepared': False, 'state': 'refused_or_incomplete', 'automatic_retry': False, 'key_material_output': False}))
        raise SystemExit(1)
