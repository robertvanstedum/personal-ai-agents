"""Explicit owner-only bundle publication with a durable pending journal.

No scheduler is installed. recover() resumes queued publications after restart.
No remote upload or backup is performed. Only store.root/transcripts is used.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

from transcript_format import RENDERER, VERSION, render
from transcript_snapshot import capture


def _json(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()


def _directory(path):
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or not path.is_dir() or path.stat().st_mode & 0o077:
        raise ValueError("Publication directory must be owner-private and not a symlink")


def _sync_dir(path):
    fd=os.open(path,os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def _write(path, content):
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),"wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def _locked(store):
    root=store.root / "transcripts"
    _directory(root)
    fd=os.open(root / ".publication.lock", os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        yield root
    finally:
        os.close(fd)


def _prepare(db):
    db.execute("""CREATE TABLE IF NOT EXISTS transcript_publications(
        id TEXT PRIMARY KEY,session TEXT NOT NULL REFERENCES rooms(id),revision INTEGER NOT NULL,
        payload TEXT NOT NULL,snapshot_at TEXT NOT NULL,generated_at TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','published')))""")


def verify(directory):
    """Verify locally generated fixed-name bundle before use; no path traversal."""
    directory=Path(directory)
    if directory.is_symlink(): raise ValueError("Symlink bundle")
    manifest_path=directory/"manifest.json"
    if manifest_path.is_symlink(): raise ValueError("Symlink manifest")
    manifest=json.loads(manifest_path.read_text())
    if set(manifest["files"]) != {"transcript.json","transcript.md"}:
        raise ValueError("Unexpected bundle files")
    for name, expected in manifest["files"].items():
        path=directory/name
        if path.is_symlink(): raise ValueError("Symlink transcript")
        content=path.read_bytes()
        if len(content)!=expected["bytes"] or hashlib.sha256(content).hexdigest()!=expected["sha256"]:
            raise ValueError("Transcript bundle integrity mismatch")
    return manifest


def _complete(store, root, row, fault):
    data=json.loads(row["payload"])
    files=render(data,snapshot_at=row["snapshot_at"])
    manifest=dict(bundle_id=row["id"], schema_version=VERSION,renderer_version=RENDERER,
        source_instance_id=data["source_instance_id"],session_id=row["session"],
        source_revision=row["revision"],through_seq=data["through_seq"],
        generated_at=row["generated_at"],snapshot_at=row["snapshot_at"],
        session_state=data["session"]["state"],
        publication_status="final" if data["session"]["state"]=="closed" else "in_progress",
        scope=data["coverage"]["scope"], files={name:dict(bytes=len(content),sha256=hashlib.sha256(content).hexdigest()) for name,content in files.items()})
    destination=root / row["id"]
    if destination.exists() or destination.is_symlink():
        if verify(destination)!=manifest: raise ValueError("Existing publication conflicts with journal")
    else:
        scratch=Path(tempfile.mkdtemp(prefix=".pending-",dir=root))
        for name,content in files.items(): _write(scratch/name,content)
        _write(scratch/"manifest.json",_json(manifest))
        verify(scratch)
        _sync_dir(scratch)
        fault("before_rename")
        os.rename(scratch,destination)
        _sync_dir(root)
        fault("after_rename")
    with store.connect() as db:
        db.execute("UPDATE transcript_publications SET state='published' WHERE id=?",(row["id"],))
    return destination


def publish(store,actor,session_id,*,fault=lambda stage:None):
    store.owner(actor)
    with _locked(store) as root:
        data,stamp=capture(store,actor,session_id)
        files=render(data,snapshot_at=stamp)
        material=b"".join(name.encode()+b"\0"+files[name] for name in sorted(files))
        identity=f"{session_id}-r{data['source_revision']}-{hashlib.sha256(material).hexdigest()}"
        with store.connect() as db:
            _prepare(db)
            db.execute("INSERT OR IGNORE INTO transcript_publications VALUES(?,?,?,?,?,?,'pending')",
                       (identity,session_id,data["source_revision"],json.dumps(data),stamp,
                        datetime.now(timezone.utc).isoformat().replace("+00:00","Z")))
            row=dict(db.execute("SELECT * FROM transcript_publications WHERE id=?",(identity,)).fetchone())
        fault("after_queue")
        return _complete(store,root,row,fault)


def recover(store,actor):
    store.owner(actor)
    with _locked(store) as root:
        with store.connect() as db:
            _prepare(db)
            pending=[dict(r) for r in db.execute("SELECT * FROM transcript_publications WHERE state='pending' ORDER BY session,revision")]
        return [_complete(store,root,row,lambda stage:None) for row in pending]
