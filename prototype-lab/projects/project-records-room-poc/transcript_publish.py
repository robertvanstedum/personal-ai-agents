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
        _write(scratch/".publication-owner.json",_json({"bundle_id":row["id"]}))
        fault("after_scratch")
        for name,content in files.items(): _write(scratch/name,content)
        _write(scratch/"manifest.json",_json(manifest))
        verify(scratch)
        (scratch/".publication-owner.json").unlink()
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


def cleanup_scratch(store,actor):
    """Quarantine positively identified scratch only AFTER verified publication.

    Never recursively delete. Unknown/malformed/symlink entries remain untouched.
    Quarantine retains recovery evidence; disk-space reclamation is an explicit
    later operator action, not an inferred cleanup permission.
    """
    store.owner(actor)
    moved,skipped=[],[]
    with _locked(store) as root:
        with store.connect() as db:
            _prepare(db)
            rows={r["id"]:dict(r) for r in db.execute("SELECT * FROM transcript_publications WHERE state='published'")}
        for scratch in sorted(root.glob(".pending-*")):
            try:
                if scratch.is_symlink() or not scratch.is_dir(): raise ValueError("unsafe scratch")
                entries=list(scratch.iterdir())
                allowed={".publication-owner.json","transcript.json","transcript.md","manifest.json"}
                if any(p.is_symlink() or not p.is_file() or p.name not in allowed for p in entries):
                    raise ValueError("unrecognized scratch content")
                marker=scratch/".publication-owner.json"
                if marker.exists():
                    identity=json.loads(marker.read_text())["bundle_id"]
                else:
                    identity=verify(scratch)["bundle_id"]
                # Lookup precedes path construction: only journal-owned names.
                row=rows.get(identity)
                if row is None: raise ValueError("no published journal")
                destination=root/identity
                manifest=verify(destination)
                data=json.loads(row["payload"])
                expected=render(data,snapshot_at=row["snapshot_at"])
                if manifest["bundle_id"]!=identity or any(
                        (destination/name).read_bytes()!=content for name,content in expected.items()):
                    raise ValueError("published data mismatch")
                quarantine=root/"scratch-quarantine"
                _directory(quarantine)
                target=quarantine/scratch.name
                if target.exists() or target.is_symlink(): raise ValueError("quarantine collision")
                os.rename(scratch,target)
                _sync_dir(quarantine)
                _sync_dir(root)
                moved.append(str(target))
            except (OSError,ValueError,KeyError,TypeError):
                skipped.append(scratch.name)
    return {"quarantined":moved,"left_untouched":skipped,"deleted":0}
