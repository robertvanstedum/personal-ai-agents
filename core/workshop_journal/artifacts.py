"""Immutable retained documents (v0.6 section 10; v0.7 Unit 2).

A handoff pins the document revision reviewed, not a path whose contents can change. A document is read once into stable bytes,
scrubbed of credentials, hashed (the hash of what was read and the hash of what is kept), and published under its kept hash:

    <workshop>/artifacts/sha256/<kept hash>            the bytes, never replaced
    <workshop>/artifacts/provenance/<read hash>.json   which read hash produced which kept hash, and how many credentials went

Order, under the workshop lock: the object is written and made durable (file and folder), then its provenance, then the event
that refers to it. A crash between the object and the event leaves an orphan, which is reported and never deleted. A forbidden
original is never written anywhere: only its hash is kept. Nothing here reads a model or the network.
"""
from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
from dataclasses import dataclass

from core.workshop_journal import fsutil, strictjson
from core.workshop_journal.errors import ArtifactCorrupt, ArtifactMissing, Missing, SourceRefused
from utils import credential_scrub

ARTIFACTS, OBJECTS, PROVENANCE = "artifacts", "sha256", "provenance"
MAX_TEXT_BYTES = 16 * 1024 * 1024
EXCLUDED_CLASSES = ("private", "excluded")
SOURCE_CLASSES = ("handoff", "spec", "evidence", "diff", "test", "conversation", "other")
SCRUBBER_VERSION = 1
HASH = re.compile(r"^[0-9a-f]{64}$")
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | (os.O_CLOEXEC if hasattr(os, "O_CLOEXEC") else 0)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Prepared:
    """A document ready to publish: nothing has been written yet."""
    original_sha256: str
    retained_sha256: str
    data: bytes                      # the kept (scrubbed) bytes
    redactions: int
    source_class: str
    original_size: int

    def ref(self, ident: str, locator: str | None = None) -> dict:
        """The event reference for this document (inert display text only in ``locator``)."""
        ref = {"type": "artifact", "id": ident, "sha256": self.retained_sha256, "availability": "retained"}
        if locator:
            ref["locator"] = locator
        return ref

    def provenance(self) -> bytes:
        return strictjson.canonical_bytes({
            "v": 1, "original_sha256": self.original_sha256, "retained_sha256": self.retained_sha256,
            "redactions": self.redactions, "source_class": self.source_class, "original_size": self.original_size,
            "retained_size": len(self.data), "scrubber": SCRUBBER_VERSION})


def prepare(data: bytes, *, source_class: str = "handoff") -> Prepared:
    """Decide what would be kept, without writing anything. Refuses with a fixed code; never truncates silently."""
    if source_class in EXCLUDED_CLASSES:
        raise SourceRefused("excluded_source")
    if source_class not in SOURCE_CLASSES:
        raise SourceRefused("unknown_source_class")
    if not isinstance(data, (bytes, bytearray)):
        raise SourceRefused("not_bytes")
    data = bytes(data)
    if len(data) > MAX_TEXT_BYTES:
        raise SourceRefused("too_large")
    if not data:
        raise SourceRefused("empty")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SourceRefused("not_text") from None
    if "\x00" in text:
        raise SourceRefused("not_text")
    kept, changed = credential_scrub.scrub(text)
    redactions = kept.count(credential_scrub.REMOVED) - text.count(credential_scrub.REMOVED) if changed else 0
    retained = kept.encode("utf-8")
    return Prepared(sha256(data), sha256(retained), retained, max(redactions, 0), source_class, len(data))


# ── reading a source safely ────────────────────────────────────────────────────────────────────────────────────────
def capture_file(root: str, relative: str, *, limit: int = MAX_TEXT_BYTES) -> bytes:
    """The stable bytes of a regular file below an approved folder.

    No component may be a link or ``..``; the file must be regular with one link; its size and modification time must not move
    while it is read, and a second read must match the first, or the answer is ``source_changed`` (nothing is returned)."""
    parts = [p for p in relative.split("/")]
    if not relative or relative.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise SourceRefused("path_not_below_root")
    try:
        fd = os.open(root, _DIR)
    except OSError as exc:
        raise SourceRefused("root_is_a_link_or_unreadable" if exc.errno in (errno.ELOOP, errno.ENOTDIR) else "root_unreadable") from None
    held = [fd]
    try:
        for part in parts[:-1]:
            try:
                fd = os.open(part, _DIR, dir_fd=fd)
            except OSError as exc:
                raise SourceRefused("folder_is_a_link" if exc.errno in (errno.ELOOP, errno.ENOTDIR) else "folder_unreadable") from None
            held.append(fd)
        name = parts[-1]
        try:
            ffd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_CLOEXEC if hasattr(os, "O_CLOEXEC") else 0), dir_fd=fd)   # a named pipe must not block the open
        except OSError as exc:
            raise SourceRefused("file_is_a_link_or_not_regular" if exc.errno in (errno.ELOOP, errno.ENXIO, errno.EISDIR)
                                else "file_unreadable") from None
        held.append(ffd)
        before = os.fstat(ffd)
        if not stat.S_ISREG(before.st_mode):
            raise SourceRefused("not_a_regular_file")
        if before.st_nlink != 1:
            raise SourceRefused("hard_linked")
        if before.st_size > limit:
            raise SourceRefused("too_large")
        first = fsutil.read_all(ffd)
        mid = os.fstat(ffd)
        second = fsutil.read_all(ffd)
        after = os.fstat(ffd)
        try:
            named = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except OSError:
            raise SourceRefused("source_changed") from None
        stamp = lambda s: (s.st_size, s.st_mtime_ns, s.st_ino, s.st_dev)               # noqa: E731
        if not (stamp(before) == stamp(mid) == stamp(after) == stamp(named)) or first != second or len(first) != before.st_size:
            raise SourceRefused("source_changed")
        return first
    finally:
        for h in held:
            try:
                os.close(h)
            except OSError:
                pass


# ── the store ──────────────────────────────────────────────────────────────────────────────────────────────────────
def _objects(base_fd: int, *, create: bool) -> tuple[int, int]:
    art = fsutil.open_subdir(base_fd, ARTIFACTS, create=create)
    try:
        return art, fsutil.open_subdir(art, OBJECTS, create=create)
    except BaseException:
        os.close(art)
        raise


def publish(base_fd: int, item: Prepared) -> bool:
    """Publish one document under the lock the caller holds. True if newly written, False if the same bytes were already kept.

    The object is made durable (file and folder) before its provenance; any failure raises and the caller appends no event."""
    art = obj = prov = None
    try:
        art, obj = _objects(base_fd, create=True)
        prov = fsutil.open_subdir(art, PROVENANCE, create=True)
        created = fsutil.publish_new(obj, item.retained_sha256, item.data)
        fsutil.publish_new(prov, f"{item.original_sha256}.json", item.provenance())
        fsutil.fsync_dir(art)
        return created
    finally:
        for fd in (prov, obj, art):
            if fd is not None:
                os.close(fd)


def open_exact(base_fd: int, retained_sha256: str) -> bytes:
    """The exact kept bytes, or an error. Reads never create anything; the hash is checked on every open."""
    if not isinstance(retained_sha256, str) or not HASH.fullmatch(retained_sha256):
        raise SourceRefused("bad_hash")
    try:
        art, obj = _objects(base_fd, create=False)
    except Missing:
        raise ArtifactMissing("no_artifacts") from None
    try:
        try:
            fd = fsutil.open_file(obj, retained_sha256, os.O_RDONLY)
        except Missing:
            raise ArtifactMissing("not_kept") from None
        try:
            data = fsutil.read_all(fd)
        finally:
            os.close(fd)
    finally:
        os.close(obj)
        os.close(art)
    if sha256(data) != retained_sha256:
        raise ArtifactCorrupt("hash_mismatch")
    return data


def inventory(base_fd: int, referenced: set[str]) -> dict:
    """Read-only report: kept objects nothing refers to (orphans), referenced hashes that are missing, damaged objects, and
    stale temporary files. Counts and hashes only; nothing is deleted."""
    report = {"objects": 0, "orphans": [], "missing_referenced": [], "corrupt": [], "without_provenance": [], "stale_temp": 0}
    try:
        art, obj = _objects(base_fd, create=False)
    except Missing:
        report["missing_referenced"] = sorted(referenced)
        return report
    prov = None
    try:
        try:
            prov = fsutil.open_subdir(art, PROVENANCE, create=False)
            have_prov = {n[:-5] for n in os.listdir(prov) if n.endswith(".json")}
            prov_docs = {}
            for n in os.listdir(prov):
                if n.endswith(".json"):
                    pfd = fsutil.open_file(prov, n, os.O_RDONLY)
                    try:
                        prov_docs[n[:-5]] = strictjson.loads(fsutil.read_all(pfd))
                    except strictjson.StrictJSONError:
                        prov_docs[n[:-5]] = {}
                    finally:
                        os.close(pfd)
        except Missing:
            have_prov, prov_docs = set(), {}
        kept_with_provenance = {d.get("retained_sha256") for d in prov_docs.values() if isinstance(d, dict)}
        names = os.listdir(obj)
        report["stale_temp"] = sum(1 for n in names if n.startswith(".tmp-"))
        present = set()
        for name in sorted(n for n in names if HASH.fullmatch(n)):
            report["objects"] += 1
            present.add(name)
            try:
                fd = fsutil.open_file(obj, name, os.O_RDONLY)
            except Exception:
                report["corrupt"].append(name)
                continue
            try:
                if sha256(fsutil.read_all(fd)) != name:
                    report["corrupt"].append(name)
            finally:
                os.close(fd)
            if name not in referenced:
                report["orphans"].append(name)
            if name not in kept_with_provenance:
                report["without_provenance"].append(name)
        report["missing_referenced"] = sorted(referenced - present)
        return report
    finally:
        if prov is not None:
            os.close(prov)
        os.close(obj)
        os.close(art)


def referenced_hashes(events: list[dict]) -> set[str]:
    """Every retained artifact hash any event refers to."""
    out = set()
    for ev in events:
        for ref in ev.get("refs") or []:
            if isinstance(ref, dict) and ref.get("type") == "artifact" and ref.get("availability") == "retained" and ref.get("sha256"):
                out.add(ref["sha256"])
    return out
