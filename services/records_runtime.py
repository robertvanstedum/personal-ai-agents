"""Explicit production Records entrypoint. Refuses unprovisioned state, no bootstrap.
Manifest creation, migration, keys/grants and service installation are operator work.
"""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,os,re,sqlite3,stat,sys
from pathlib import Path
from minimoi_portal.transport_profiles import PRODUCTION, select_records_profile
APP_DIR=Path(__file__).resolve().parents[1]/'prototype-lab/projects/project-records-room-poc'
MANIFEST='production-runtime.json'
KEY_PATH='/run/secrets/rooms-bridge/portal-bridge.key'
REQUIRED_TABLES={'credential_audit', 'cos_auto', 'web_sessions', 'notes', 'events', 'operations', 'hosted_teammates', 'rooms', 'presence', 'cos_auto_audit', 'executive_snapshots', 'documents', 'room_transfers', 'turn_order', 'room_archive', 'agent_credential_generations', 'meetings', 'artifact_refs', 'meta', 'client_installations', 'cos_auto_jobs', 'persistent_rooms', 'coordination_steps', 'credential_scopes', 'disclosure_grants', 'member_rsvp', 'agent_approvals', 'agent_approval_audit', 'principals', 'teammates', 'routing_notes', 'turns', 'room_generations', 'coordination_items', 'members', 'cos_requests', 'teammate_status', 'client_credentials', 'portal_nonces'}
def refuse(ok,code):
    if not ok:raise ValueError(code)
def private_file(path,maximum,*,metadata_only=False):
    path=Path(path)
    refuse(path.is_absolute(),'absolute_path_required')
    for p in (path,*path.parents):refuse(not p.is_symlink(),'symlink_refused')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        s=os.fstat(fd);refuse(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_uid in {0,os.geteuid()} and not s.st_mode&0o077 and s.st_size<=maximum,'private_file_required')
        if metadata_only:return None
        raw=os.read(fd,maximum+1);z=os.fstat(fd);refuse(len(raw)==s.st_size and len(raw)<=maximum and (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)==(z.st_dev,z.st_ino,z.st_size,z.st_mtime_ns),'file_changed');return raw
    finally:os.close(fd)
def schema_hash(db):
    rows=db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    return hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest()
def preflight(data_dir,environ,*,port=18880):
    """Read-only verification BEFORE Store can create a DB/key/owner/schema."""
    env=dict(environ)
    refuse(select_records_profile(env.get('BASE_URL'),env)==PRODUCTION,'explicit_production_profile_required')
    refuse(env.get('RECORDS_PORTAL_ENV')=='production' and env.get('RECORDS_ALLOWED_HOSTS')=='minimoi-records:18880' and port==18880,'production_runtime_profile_mismatch')
    root=Path(data_dir);refuse(root.is_absolute() and str(root)==os.path.normpath(str(root)) and not root.is_symlink(),'private_data_root_required')
    for p in (root,*root.parents):refuse(not p.is_symlink(),'symlink_refused')
    s=root.stat();refuse(stat.S_ISDIR(s.st_mode) and s.st_uid==os.geteuid() and not s.st_mode&0o077,'private_data_root_required')
    refuse(not os.path.lexists(root/'INCOMPLETE'),'preparation_incomplete')
    marker=json.loads(private_file(root/MANIFEST,16384))
    required={'format','environment','data_dir','schema_sha256','owner_key_sha256','session_key_sha256','portal_key_kid'}
    refuse(isinstance(marker,dict) and set(marker)==required and marker['format']=='records-production-runtime/1' and marker['environment']=='production' and marker['data_dir']==str(root),'prepared_manifest_required')
    key_path=env.get('RECORDS_PORTAL_KEY_FILE');refuse(key_path==KEY_PATH,'production_key_reference_required')
    key=private_file(key_path,4096).decode().strip();kid,sep,secret=key.partition(':')
    refuse(bool(sep) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,64}',kid)) and len(secret)>=32 and kid==marker['portal_key_kid'],'prepared_portal_key_required')
    owner=private_file(root/'owner-key.txt',4096).strip();session=private_file(root/'session-key.txt',4096).strip()
    refuse(len(owner)>=32 and len(session)>=32 and hashlib.sha256(owner).hexdigest()==marker['owner_key_sha256'] and hashlib.sha256(session).hexdigest()==marker['session_key_sha256'],'prepared_store_keys_required')
    private_file(root/'records.sqlite3',512*1024*1024,metadata_only=True)
    with sqlite3.connect((root/'records.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        refuse(schema_hash(db)==marker['schema_sha256'],'prepared_schema_mismatch')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")};refuse(REQUIRED_TABLES<=tables,'prepared_schema_incomplete')
        refuse(db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()==('5',),'prepared_schema_version_required')
        refuse(db.execute("SELECT value FROM meta WHERE key='rooms_r1_schema'").fetchone()==('1',),'prepared_rooms_schema_required')
        refuse(db.execute("SELECT token_hash FROM principals WHERE id='robert'").fetchone()==(hashlib.sha256(owner).hexdigest(),),'prepared_owner_required')
        # Existing legacy import and room-generation setup must already exist;
        # Store startup must not be what creates credentials or backfills data.
        missing=db.execute("SELECT COUNT(*) FROM principals p LEFT JOIN client_installations i ON i.id='legacy:'||p.id LEFT JOIN client_credentials c ON c.id='legacy:'||p.id WHERE i.id IS NULL OR c.id IS NULL").fetchone()[0]
        refuse(missing==0,'prepared_legacy_import_required')
        refuse(db.execute('SELECT COUNT(*) FROM rooms WHERE id NOT IN (SELECT room FROM room_generations)').fetchone()[0]==0,'prepared_generations_required')
    return {'profile':'production','schema_sha256':marker['schema_sha256'],'provisioned_here':False}
def build_app(data_dir,environ):
    preflight(data_dir,environ)
    if str(APP_DIR) not in sys.path:sys.path.append(str(APP_DIR))
    spec=importlib.util.spec_from_file_location('minimoi_production_records_app',APP_DIR/'app.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.create_app(data_dir,port=18880,runtime_environ=dict(environ))
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-dir',required=True);p.add_argument('--execute',action='store_true');args=p.parse_args();preflight(args.data_dir,os.environ)
    if not args.execute:print(json.dumps({'plan_only':True,'profile':'production','provisioned_here':False}));return
    app=build_app(args.data_dir,os.environ)
    import logging
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    app.run(host='0.0.0.0',port=18880,debug=False,use_reloader=False,threaded=True)
if __name__=='__main__':main()
