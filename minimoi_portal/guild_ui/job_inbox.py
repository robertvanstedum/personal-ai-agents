"""A job-scoped inbox: private copies of the owner's original files that one Master Craftsman job may read.

A job never reads the conversation's own document store. When a job starts, the files the owner named are COPIED here
(``<guild data>/jobs/inbox/<job id>/``), each verified against the hash stored when it was uploaded, and a MANIFEST
lists what is there. Ids are resolved on the server against the owner's conversation; nothing comes from a path the
caller supplied. The folder is private (0700, files 0400). Removing a document from a conversation does NOT delete a
copy already made here; a sweep removes an inbox after a week. Overnight build, step 2 (amendment B3)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time

from .conversations import (ConversationNotFound, ConversationStoreUnavailable, DOC_ID_RE, DocumentGone,
                            ORIGINAL_READ_MAX)

INBOX_FOLDER = os.path.join("jobs", "inbox")
JOB_ID_RE = re.compile(r"^j-[0-9a-f]{16}$")
FILE_MAX_BYTES = 5 * 1024 * 1024
ALL_INBOXES_MAX_BYTES = 200 * 1024 * 1024
SWEEP_AFTER_SECONDS = 7 * 24 * 3600
MANIFEST = "MANIFEST.json"


class InboxRefused(RuntimeError):
    """The inbox could not be made as asked; ``code`` says why and nothing partial is left behind."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def inbox_root(folder: str) -> str:
    return os.path.join(folder, INBOX_FOLDER)


def inbox_path(folder: str, job_id: str) -> str:
    if not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
        raise InboxRefused("bad_job_id", "That is not a job id.")
    return os.path.join(inbox_root(folder), job_id)


def _safe_name(name: str, index: int) -> str:
    base = re.sub(r"[^\w.\-]", "_", os.path.basename(str(name or "file")))[:80].lstrip(".") or "file"
    return f"{index:02d}-{base}"


def inboxes_bytes(folder: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(inbox_root(folder)):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def stage(store, folder: str, cid: str, principal: str, job_id: str, doc_ids: list) -> dict:
    """Copy the named originals of one conversation into the job's inbox and return the manifest
    {job_id, files: [{doc_id, name, path, bytes, sha256}]}. All or nothing: any refusal removes the partial inbox."""
    if not folder:
        raise InboxRefused("unavailable", "No data folder is configured, so no files can be given to a job.")
    target = inbox_path(folder, job_id)
    if not isinstance(doc_ids, list) or not doc_ids or len(doc_ids) > 5 or len(set(doc_ids)) != len(doc_ids):
        raise InboxRefused("bad_ids", "Name between one and five different files for a job.")
    if any(not isinstance(d, str) or not DOC_ID_RE.fullmatch(d) for d in doc_ids):
        raise InboxRefused("bad_ids", "One of the file ids is not valid.")
    if os.path.lexists(target):
        raise InboxRefused("exists", "That job already has an inbox.")
    gathered = []
    for doc_id in doc_ids:
        try:
            entry, data = store.read_original(cid, principal, doc_id)
        except ConversationNotFound:
            raise InboxRefused("not_found", "That conversation was not found.") from None
        except DocumentGone:
            raise InboxRefused("no_original", "No original was kept for one of those files, so a job cannot read it.") from None
        except ConversationStoreUnavailable:
            raise InboxRefused("unavailable", "The files could not be read right now. Nothing was started.") from None
        if len(data) > FILE_MAX_BYTES:
            raise InboxRefused("too_big", "One of those files is over 5 MB.")
        gathered.append((doc_id, entry, data))
    if inboxes_bytes(folder) + sum(len(d) for _i, _e, d in gathered) > ALL_INBOXES_MAX_BYTES:
        raise InboxRefused("quota", "The job inboxes are full. Wait for older ones to be cleared (after 7 days) and try again.")
    manifest = {"job_id": job_id, "files": []}
    try:
        os.makedirs(target, mode=0o700)
        for index, (doc_id, entry, data) in enumerate(gathered, 1):
            name = _safe_name(entry.get("name"), index)
            path = os.path.join(target, name)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o400)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            digest = hashlib.sha256(data).hexdigest()
            with open(path, "rb") as check:                 # the copy itself is verified, not the bytes we meant to write
                if hashlib.sha256(check.read(ORIGINAL_READ_MAX)).hexdigest() != digest:
                    raise InboxRefused("copy_mismatch", "A copy did not match its original, so nothing was started.")
            manifest["files"].append({"doc_id": doc_id, "name": entry.get("name"), "path": name, "bytes": len(data), "sha256": digest})
        mpath = os.path.join(target, MANIFEST)
        fd = os.open(mpath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
    except InboxRefused:
        shutil.rmtree(target, ignore_errors=True)
        raise
    except OSError as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise InboxRefused("unavailable", f"The files could not be copied ({type(exc).__name__}). Nothing was started.") from exc
    return manifest


def remove_inbox(folder: str, job_id: str) -> bool:
    """Delete one job's inbox (its copies only; never the conversation's own files). True when something was removed."""
    path = inbox_path(folder, job_id)
    if not os.path.isdir(path) or os.path.islink(path):
        return False
    shutil.rmtree(path)
    return True


def sweep(folder: str, *, now: float | None = None, older_than: float = SWEEP_AFTER_SECONDS, keep: set | None = None) -> list:
    """Remove inboxes older than a week (by their manifest's mtime), except those of jobs named in ``keep`` (running).
    Returns the removed job ids; anything that is not a job inbox folder is left alone."""
    now = time.time() if now is None else now
    removed = []
    root = inbox_root(folder)
    if not os.path.isdir(root):
        return removed
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not JOB_ID_RE.fullmatch(name) or os.path.islink(path) or not os.path.isdir(path) or name in (keep or ()):
            continue
        try:
            if now - os.lstat(path).st_mtime >= older_than:
                shutil.rmtree(path)
                removed.append(name)
        except OSError:
            continue
    return removed
