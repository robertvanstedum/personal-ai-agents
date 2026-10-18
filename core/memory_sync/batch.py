"""The sync batch: what one ``vault-sync`` run sends and one ``vault-apply`` run verifies (17 Oct 2026). Both sides use only this module.

Objects, under ``<dest>/sync/batches/<batch_id>/`` (``batch_id`` is the snapshot transport's attempt id: UTC time + random hex):

  ``batch.tar.age``   the batch tar (gzip), age-encrypted to the existing PUBLIC recipient. Members, in order:
                      ``BATCH.json`` (format, batch id, and per version: record id, kind, folder name, edition, edition hash, digest,
                      source key, its ``source_index.json`` entry, and every file's (rel, sha256, size)), then
                      ``records/<kind>/<name>/<rel>`` for every file. Regular files only, uid/gid 0, mode 0600, sorted.
  ``manifest.json``   plain text, content-free: batch id, created time, host, the versions as (record id, edition, edition hash,
                      digest), counts, the ciphertext's and plaintext's sha256 and size, the age version and recipient fingerprint.
  ``COMPLETE.json``   written LAST: the manifest's sha256 and the ciphertext's sha256 and size. A batch without it is an orphan that
                      production never lists, so an upload killed part way leaves nothing to apply.

Every object is a conditional create (``If-None-Match: *`` on S3, an exclusive link on ``file://``): nothing is ever overwritten.

Acknowledgements, under ``<dest>/sync/acks/`` (written by production only): ``<batch_id>.json`` with a status per version
(``stored`` | ``superseded`` | ``rejected:<code>``) and ``inventory.json`` (``versions.inventory_doc``). Ids, hashes and codes only.

Reading a file for the tar hashes the very bytes written into it; a file that changed since the inventory read it (a capture wrote a new
edition meanwhile) drops that record from this batch (``changed_during_read``) and the next run sends its new version. So a batch's
manifest always describes exactly the bytes it carries.

On EC2, ``extract`` is strict (regular files and folders only; nothing absolute, climbing, duplicated or outside ``BATCH.json`` and
``records/<kind>/<name>/``) and ``check_version`` verifies one version against the extracted bytes: the file set equals the listed
set, every sha256 and size matches, the digest recomputes, and the main file's front matter names the same id, edition and hash.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import platform
import re
import tarfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

from core.memory_backup import backup as B
from core.memory_backup.backup import BackupError
from core.memory_sync import versions as V

MAX_BATCH_DOC = 64 << 20
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")       # a record folder name (title slug + short ulid)
HOSTNAME = re.compile(r"[^A-Za-z0-9.-]")


def _source_host() -> str:
    return (HOSTNAME.sub("", platform.node().split(".")[0]) or "unknown")[:40]


def _member_ok(rel: str) -> bool:
    p = Path(rel)
    return bool(rel) and not p.is_absolute() and ".." not in p.parts and "\0" not in rel and "\\" not in rel and rel == p.as_posix()


class _HashingReader(io.RawIOBase):
    """Feeds the tar exactly ``size`` bytes from ``f`` and hashes what it handed over."""

    def __init__(self, f):
        self.f, self.h, self.n = f, hashlib.sha256(), 0

    def readable(self):
        return True

    def readinto(self, buf):
        data = self.f.read(len(buf))
        self.h.update(data)
        self.n += len(data)
        buf[: len(data)] = data
        return len(data)


def write_tar(shelf_root, chosen: list, sources: dict, batch_id: str, out: Path) -> dict:
    """Write the (plaintext) batch tar for ``chosen`` versions. Returns ``{"versions": [...], "dropped": [record ids], "bytes": n}``.
    A record whose bytes no longer match its version is dropped and the tar rewritten without it (at most once per record)."""
    root = Path(shelf_root)
    keep = list(chosen)
    while True:
        dropped = _try_tar(root, keep, sources, batch_id, out)
        if not dropped:
            return {"versions": keep, "dropped": [v.record_id for v in chosen if v not in keep], "bytes": out.stat().st_size}
        keep = [v for v in keep if v.record_id not in dropped]
        if not keep:
            out.unlink(missing_ok=True)
            return {"versions": [], "dropped": [v.record_id for v in chosen], "bytes": 0}


def batch_doc(batch_id: str, chosen: list, sources: dict) -> dict:
    out = []
    for v in chosen:
        entry = sources.get(v.source_key) if v.source_key else None
        out.append({"record_id": v.record_id, "kind": v.kind, "name": v.name, "edition": v.edition, "edition_hash": v.edition_hash,
                    "digest": v.digest, "source_key": v.source_key, "source_index_entry": entry if isinstance(entry, dict) else None,
                    "files": [[rel, sha, size] for rel, sha, size in v.files]})
    return {"format": V.BATCH_FORMAT, "batch_id": batch_id, "versions": out}


def _try_tar(root: Path, chosen: list, sources: dict, batch_id: str, out: Path) -> set:
    doc = json.dumps(batch_doc(batch_id, chosen, sources), sort_keys=True, indent=1).encode("utf-8")
    dropped = set()
    with tarfile.open(out, "w:gz", format=tarfile.PAX_FORMAT, compresslevel=6) as tar:
        info = tarfile.TarInfo(V.BATCH_DOC)
        info.size, info.mode, info.mtime = len(doc), 0o600, 0
        tar.addfile(info, io.BytesIO(doc))
        for v in chosen:
            for rel, sha, size in v.files:
                B.batch_timeout()
                info = tarfile.TarInfo(f"{V.RECORDS_ROOT}/{v.kind}/{v.name}/{rel}")
                info.size, info.mode, info.mtime = int(size), 0o600, 0
                path = root / v.kind / v.name / rel
                try:
                    with open(path, "rb") as f:
                        reader = _HashingReader(f)
                        tar.addfile(info, reader)                   # tarfile reads exactly info.size bytes through the hasher
                        extra = f.read(1)
                except (OSError, tarfile.TarError):
                    dropped.add(v.record_id)
                    break
                if reader.n != int(size) or reader.h.hexdigest() != sha or extra:
                    dropped.add(v.record_id)
                    break
    return dropped


def manifest(batch_id: str, chosen: list, *, created: datetime, cipher: Path, plain_sha: str, plain_bytes: int, age_version: str,
             recipient: str, mode: str) -> dict:
    return {"format": V.BATCH_FORMAT, "batch_id": batch_id, "created_utc": created.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_host": _source_host(), "mode": mode, "count": len(chosen),
            "record_bytes": sum(v.bytes for v in chosen), "versions": sorted((v.key() for v in chosen), key=lambda e: e["record_id"]),
            "objects": {"batch": {"name": V.BATCH_OBJECT, "sha256": B.sha256_file(cipher), "bytes": cipher.stat().st_size,
                                  "plaintext_sha256": plain_sha, "plaintext_bytes": plain_bytes}},
            "age": {"version": age_version, "recipient_fingerprint": B.fingerprint(recipient)}}


def complete(m: dict, manifest_sha: str) -> dict:
    return {"format": V.BATCH_FORMAT, "batch_id": m["batch_id"], "created_utc": m["created_utc"], "manifest_sha256": manifest_sha,
            "objects": {"batch": {"sha256": m["objects"]["batch"]["sha256"], "bytes": m["objects"]["batch"]["bytes"]}}}


def upload(d: B.Dest, batch_id: str, cipher: Path, manifest_path: Path, complete_path: Path, *, put=None) -> None:
    """Ciphertext, manifest, then COMPLETE last. Each a conditional create."""
    put = put or d.put
    folder = f"{V.BATCH_PREFIX}/{batch_id}"
    put(cipher, f"{folder}/{V.BATCH_OBJECT}")
    put(manifest_path, f"{folder}/{B.MANIFEST}")
    put(complete_path, f"{folder}/{B.COMPLETE}")


# ── EC2 ──
def complete_batches(d: B.Dest) -> list[str]:
    """Batch ids that have COMPLETE.json, oldest first. Orphans (no COMPLETE) are never listed."""
    ids = set()
    for key in d.list_keys(V.BATCH_PREFIX):
        parts = key.split("/")
        if len(parts) == 4 and parts[3] == B.COMPLETE and B.ATTEMPT.match(parts[2]):
            ids.add(parts[2])
    return sorted(ids)


def _json(path: Path, code: str) -> dict:
    try:
        doc = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        raise BackupError(code) from None
    if not isinstance(doc, dict):
        raise BackupError(code)
    return doc


def fetch_chain(d: B.Dest, batch_id: str, scratch: Path) -> tuple[dict, dict]:
    """COMPLETE -> manifest -> (later) ciphertext, each hash checked against the one before. Returns (complete, manifest)."""
    folder = f"{V.BATCH_PREFIX}/{batch_id}"
    cpath, mpath = scratch / B.COMPLETE, scratch / B.MANIFEST
    d.get(f"{folder}/{B.COMPLETE}", cpath)
    comp = _json(cpath, "complete_unreadable")
    d.get(f"{folder}/{B.MANIFEST}", mpath)
    if B.sha256_file(mpath) != comp.get("manifest_sha256"):
        raise BackupError("manifest_mismatch")
    m = _json(mpath, "manifest_unreadable")
    try:
        if m.get("format") != V.BATCH_FORMAT or comp.get("format") != V.BATCH_FORMAT or m["batch_id"] != batch_id or comp["batch_id"] != batch_id:
            raise BackupError("manifest_format")
        if comp["objects"]["batch"] != {"sha256": m["objects"]["batch"]["sha256"], "bytes": m["objects"]["batch"]["bytes"]}:
            raise BackupError("manifest_mismatch")
        for e in m["versions"]:
            if not V.ULID.match(str(e["record_id"])) or not V.HEX64.match(str(e["digest"])) or not V.HEX64.match(str(e["edition_hash"])) \
                    or isinstance(e["edition"], bool) or not isinstance(e["edition"], int):
                raise BackupError("manifest_format")
        int(m["objects"]["batch"]["plaintext_bytes"])
    except (KeyError, TypeError, ValueError):
        raise BackupError("manifest_format") from None
    return comp, m


def fetch_ciphertext(d: B.Dest, batch_id: str, m: dict, local: Path) -> None:
    d.get(f"{V.BATCH_PREFIX}/{batch_id}/{V.BATCH_OBJECT}", local)
    want = m["objects"]["batch"]
    if local.stat().st_size != int(want["bytes"]) or B.sha256_file(local) != want["sha256"]:
        raise BackupError("ciphertext_mismatch")


def declared_members(doc: dict) -> dict:
    """``records/<kind>/<name>/<rel>`` -> declared size, for every file ``BATCH.json`` lists. Raises ``batch_doc_unreadable``."""
    out = {}
    try:
        for e in doc["versions"]:
            for rel, _sha, size in e["files"]:
                if isinstance(size, bool) or not isinstance(size, int) or size < 0 or not isinstance(rel, str):
                    raise ValueError
                out[f"{V.RECORDS_ROOT}/{e['kind']}/{e['name']}/{rel}"] = size
    except (KeyError, TypeError, ValueError):
        raise BackupError("batch_doc_unreadable") from None
    return out


def extract(archive: Path, target: Path, *, max_bytes: int | None = None) -> dict:
    """See ``_extract``. A stream that is not a whole gzip tar (truncated, corrupt) is ``archive_unsafe`` (a fixed integrity code), never a
    raw exception that would fail every run without a verdict."""
    try:
        return _extract(archive, target, max_bytes=max_bytes)
    except (tarfile.TarError, EOFError, zlib.error, gzip.BadGzipFile):
        raise BackupError("archive_unsafe") from None


def _extract(archive: Path, target: Path, *, max_bytes: int | None = None) -> dict:
    """Strict extraction of a decrypted batch into ``target`` (0700 folders, 0600 files). Returns the parsed ``BATCH.json``.
    Every member after ``BATCH.json`` must be a file it declares, at exactly the declared size, so the bytes written are bounded by the
    declared total (a forged batch cannot be a decompression bomb); ``max_bytes`` bounds that total (``batch_too_large``).
    Raises ``archive_unsafe`` / ``batch_doc_unreadable`` / ``batch_too_large``."""
    target.mkdir(mode=0o700, exist_ok=True)
    doc = None
    seen = set()
    declared: dict = {}
    with tarfile.open(archive, "r:gz") as tar:
        for i, member in enumerate(tar):
            B.batch_timeout()
            rel = member.name
            if not _member_ok(rel) or rel in seen or not member.isfile():
                raise BackupError("archive_unsafe")
            seen.add(rel)
            if i == 0:
                if rel != V.BATCH_DOC or member.size > MAX_BATCH_DOC:
                    raise BackupError("archive_unsafe")
                try:
                    doc = json.loads(tar.extractfile(member).read().decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    raise BackupError("batch_doc_unreadable") from None
                if not isinstance(doc, dict):
                    raise BackupError("batch_doc_unreadable")
                declared = declared_members(doc)
                if max_bytes is not None and sum(declared.values()) > max_bytes:
                    raise BackupError("batch_too_large")
                continue
            if declared.get(rel) != member.size:                    # undeclared, or not the declared size
                raise BackupError("archive_unsafe")
            parts = rel.split("/")
            if len(parts) < 4 or parts[0] != V.RECORDS_ROOT or parts[1] not in V.RECORD_DIRS or not NAME.match(parts[2]):
                raise BackupError("archive_unsafe")
            dest = target / rel                                     # only regular-file members exist, so no link can be in the way
            src = tar.extractfile(member)
            try:
                dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            except OSError:                                         # a duplicate, or a file where a folder must be
                raise BackupError("archive_unsafe") from None
            with os.fdopen(fd, "wb") as out:
                while True:
                    block = src.read(1 << 20)
                    if not block:
                        break
                    out.write(block)
    if not isinstance(doc, dict) or doc.get("format") != V.BATCH_FORMAT or not isinstance(doc.get("versions"), list):
        raise BackupError("batch_doc_unreadable")
    return doc


def doc_matches_manifest(doc: dict, m: dict) -> bool:
    """The encrypted BATCH.json and the plain manifest describe the same versions."""
    try:
        inner = sorted(({"record_id": e["record_id"], "edition": e["edition"], "edition_hash": e["edition_hash"], "digest": e["digest"]}
                        for e in doc["versions"]), key=lambda e: e["record_id"])
    except (KeyError, TypeError):
        return False
    return doc.get("batch_id") == m.get("batch_id") and inner == m.get("versions")


def check_version(extracted: Path, entry: dict) -> str | None:
    """``None`` when one version in the extracted batch is whole and exactly what it claims; otherwise a fixed rejection code."""
    try:
        kind, name, rid = entry["kind"], entry["name"], entry["record_id"]
        files = [(str(rel), str(sha), int(size)) for rel, sha, size in entry["files"]]
        edition, edition_hash, digest = entry["edition"], entry["edition_hash"], entry["digest"]
    except (KeyError, TypeError, ValueError):
        return "entry_malformed"
    if kind not in V.RECORD_DIRS or not isinstance(name, str) or not NAME.match(name) or not V.ULID.match(str(rid)):
        return "unsafe_name"
    if any(not _member_ok(rel) for rel, _, _ in files) or f"{name}.md" not in {rel for rel, _, _ in files}:
        return "entry_malformed"
    folder = extracted / V.RECORDS_ROOT / kind / name
    if not folder.is_dir():
        return "file_missing"
    present = set()
    for dirpath, _dirs, names in os.walk(folder, followlinks=False):
        for n in names:
            present.add((Path(dirpath) / n).relative_to(folder).as_posix())
    listed = {rel for rel, _, _ in files}
    if present - listed:
        return "unexpected_file"
    if listed - present:
        return "file_missing"
    for rel, sha, size in files:
        path = folder / rel
        if path.stat().st_size != size:
            return "file_size_mismatch"
        if V.sha256_file(path) != sha:
            return "file_hash_mismatch"
    if V.version_digest(kind, name, files) != digest:
        return "digest_mismatch"
    try:
        meta = V.read_front_matter(folder / f"{name}.md")
    except (V.Damaged, OSError):
        return "record_unreadable"
    if str(meta.get("id")) != rid or meta.get("edition") != edition or str(meta.get("edition_hash")) != edition_hash:
        return "front_matter_mismatch"
    if entry.get("source_key") is not None and str(meta.get("source") or "") != entry["source_key"]:
        return "front_matter_mismatch"
    return None
