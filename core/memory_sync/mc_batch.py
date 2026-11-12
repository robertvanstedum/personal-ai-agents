"""Production MC conversations to the Mac, automatically (17 Oct 2026): the batch both sides use.

The Vault's sync runs Mac -> production. Master Craftsman conversations kept in PRODUCTION need the reverse trip first: production's
exporter output reaches the Mac's ``minimoi-mc`` watch folder, the Mac captures it as ordinary records, and ``vault-sync`` carries the
records back to production as versions. This module is the contract between the two light jobs that do the reverse trip:

  EC2 ``vault-mc-export`` (``core/memory_sync/mc_export.py``)  ->  ``<dest>/sync/mc-export/<batch_id>/``
      ``batch.tar.age``  the changed export files (``production--<conversation id>.json``), age-encrypted to the existing PUBLIC recipient;
                         first member ``BATCH.json`` (format, batch id, instance, every file's name, sha256 and size)
      ``manifest.json``  plain text, content-free: batch id, instance, created time, file names (conversation ids, never titles), each
                         file's sha256 and size, the ciphertext's and plaintext's sha256 and size, the age version and recipient fingerprint
      ``COMPLETE.json``  LAST: the manifest's sha256 and the ciphertext's sha256 and size. Without it the batch is an orphan, never pulled.
  Mac ``vault-mc-pull`` (``core/memory_sync/mc_pull.py``)  ->  ``<dest>/sync/mc-export-acks/<batch_id>.json``
      per file ``placed`` (in the watch folder, durably) | ``superseded`` (a newer batch already placed newer bytes) | ``rejected:<code>``.

Every object is a conditional create except the acknowledgement (one writer per batch, rewritten whole). IAM keeps the prefixes apart:
the EC2 role writes ``sync/mc-export/``, the Mac writes ``sync/mc-export-acks/``.

The export file names are the exporter's own (``<instance>--<c-12 hex | legacy-16 hex>.json``); the pattern below is kept equal to
``core/memory_shelf/mc.py`` ``FILE`` by a test (this module stays stdlib-only for the EC2 tools bundle).

**Families (11 Nov 2026, #509).** Production CoS conversations travel the same path (``core/memory_sync/cos_batch.py``). What differs is
a ``Family``: its formats, its two S3 prefixes, its file-name pattern and the export shape each file must have. Every function below takes
an optional ``fam``; without one it is MC's, byte for byte as before.
"""
from __future__ import annotations

import errno
import gzip
import io
import json
import os
import re
import tarfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

from core.memory_backup import backup as B
from core.memory_backup.backup import BackupError
from core.memory_sync import batch as BT
from core.memory_sync import versions as V

FORMAT = "vault-mc-export/1"
ACK_FORMAT = "vault-mc-export-ack/1"
EXPORT_PREFIX = "sync/mc-export"
ACK_PREFIX = "sync/mc-export-acks"
BATCH_OBJECT = "batch.tar.age"
BATCH_DOC = "BATCH.json"
PLACED, SUPERSEDED, REJECTED = "placed", "superseded", "rejected"
FILE = re.compile(r"^([a-z][a-z0-9-]{0,31})--(c-[0-9a-f]{12}|legacy-[0-9a-f]{16})\.json$")     # == core/memory_shelf/mc.py FILE
MAX_FILE_BYTES = 48 * 1024 * 1024                                                               # == core/memory_shelf/mc.py MAX_BYTES
MAX_DOC_BYTES = 16 << 20


class Family:
    """What differs between the conversation families that travel this path. ``file`` names ``<instance>--<conversation id>.json`` with
    the instance as group 1 and the conversation id as group 2; ``doc_ok(doc, instance, conversation_id)`` is the export shape a file must
    have for its own name (content is read, never logged)."""

    __slots__ = ("name", "format", "ack_format", "export_prefix", "ack_prefix", "file", "doc_ok", "max_file_bytes")

    def __init__(self, *, name, fmt, ack_fmt, export_prefix, ack_prefix, file, doc_ok, max_file_bytes=MAX_FILE_BYTES):
        self.name, self.format, self.ack_format = name, fmt, ack_fmt
        self.export_prefix, self.ack_prefix = export_prefix, ack_prefix
        self.file, self.doc_ok, self.max_file_bytes = file, doc_ok, max_file_bytes


def _mc_doc_ok(doc, instance: str, conversation_id: str) -> bool:
    return isinstance(doc, dict) and doc.get("schema") == "minimoi.mc-conversation/1" and doc.get("instance") == instance \
        and doc.get("conversation_id") == conversation_id


MC = Family(name="mc", fmt=FORMAT, ack_fmt=ACK_FORMAT, export_prefix=EXPORT_PREFIX, ack_prefix=ACK_PREFIX, file=FILE, doc_ok=_mc_doc_ok)


def name_ok(name: str, instance: str | None = None, fam: Family | None = None) -> bool:
    fam = fam or MC
    m = fam.file.fullmatch(name or "") if isinstance(name, str) else None     # fullmatch: "$" alone would also accept a trailing newline
    return bool(m) and (instance is None or m.group(1) == instance)


# ── EC2: writing a batch ──
def write_tar(files: list, batch_id: str, instance: str, out: Path, fam: Family | None = None) -> dict:
    """``files`` = [(name, path, sha256, size)]. The tar holds ``BATCH.json`` then each file, its bytes hashed as written; a file whose
    bytes no longer match is refused (``export_changed_during_batch``): the caller exports again next run."""
    fam = fam or MC
    for name, _path, sha, size in files:
        if not name_ok(name, instance, fam) or not V.HEX64.match(sha) or int(size) > fam.max_file_bytes:
            raise BackupError("export_file_malformed")
    doc = json.dumps({"format": fam.format, "batch_id": batch_id, "instance": instance,
                      "files": [[n, s, int(z)] for n, _p, s, z in files]}, sort_keys=True, indent=1).encode("utf-8")
    with tarfile.open(out, "w:gz", format=tarfile.PAX_FORMAT, compresslevel=6) as tar:
        info = tarfile.TarInfo(BATCH_DOC)
        info.size, info.mode, info.mtime = len(doc), 0o600, 0
        tar.addfile(info, io.BytesIO(doc))
        for name, path, sha, size in files:
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = int(size), 0o600, 0
            try:
                with open(path, "rb") as f:
                    reader = BT._HashingReader(f)
                    tar.addfile(info, reader)
                    extra = f.read(1)
            except (OSError, tarfile.TarError):                    # shorter than listed, or gone
                raise BackupError("export_changed_during_batch") from None
            if reader.n != int(size) or reader.h.hexdigest() != sha or extra:
                raise BackupError("export_changed_during_batch")
    return {"files": [[n, s, int(z)] for n, _p, s, z in files], "bytes": out.stat().st_size}


def manifest(batch_id: str, instance: str, files: list, *, created: datetime, cipher: Path, plain_sha: str, plain_bytes: int,
             age_version: str, recipient: str, fam: Family | None = None) -> dict:
    return {"format": (fam or MC).format, "batch_id": batch_id, "instance": instance,
            "created_utc": created.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "count": len(files),
            "file_bytes": sum(int(z) for _n, _s, z in files), "files": sorted([n, s, int(z)] for n, s, z in files),
            "objects": {"batch": {"name": BATCH_OBJECT, "sha256": B.sha256_file(cipher), "bytes": cipher.stat().st_size,
                                  "plaintext_sha256": plain_sha, "plaintext_bytes": plain_bytes}},
            "age": {"version": age_version, "recipient_fingerprint": B.fingerprint(recipient)}}


def complete(m: dict, manifest_sha: str, fam: Family | None = None) -> dict:
    return {"format": (fam or MC).format, "batch_id": m["batch_id"], "created_utc": m["created_utc"], "manifest_sha256": manifest_sha,
            "objects": {"batch": {"sha256": m["objects"]["batch"]["sha256"], "bytes": m["objects"]["batch"]["bytes"]}}}


def upload(d: B.Dest, batch_id: str, cipher: Path, manifest_path: Path, complete_path: Path, *, put=None,
           fam: Family | None = None) -> None:
    put = put or d.put
    folder = f"{(fam or MC).export_prefix}/{batch_id}"
    put(cipher, f"{folder}/{BATCH_OBJECT}")
    put(manifest_path, f"{folder}/{B.MANIFEST}")
    put(complete_path, f"{folder}/{B.COMPLETE}")                    # last


def read_ack(d: B.Dest, batch_id: str, work: Path, fam: Family | None = None) -> dict | None:
    """The Mac's acknowledgement for one batch, or ``None`` when there is none yet. ``ack_malformed`` when it does not parse."""
    fam = fam or MC
    local = work / f"{fam.name}-ack-{batch_id}.json"
    if not d.get_optional(f"{fam.ack_prefix}/{batch_id}.json", local):
        return None
    doc = V.read_json(local)
    local.unlink(missing_ok=True)
    return parse_ack(doc, batch_id, fam)


def parse_ack(doc, batch_id: str, fam: Family | None = None) -> dict:
    fam = fam or MC
    try:
        ok = (isinstance(doc, dict) and doc.get("format") == fam.ack_format and doc.get("batch_id") == batch_id
              and isinstance(doc.get("files"), list)
              and all(isinstance(e, list) and len(e) == 3 and name_ok(str(e[0]), None, fam) and V.HEX64.match(str(e[1])) and _status_ok(e[2])
                      for e in doc["files"]))
    except TypeError:
        ok = False
    if not ok:
        raise BackupError("ack_malformed")
    return doc


def _status_ok(value) -> bool:
    if value in (PLACED, SUPERSEDED):
        return True
    return isinstance(value, str) and value.startswith(f"{REJECTED}:") and bool(V.CODE.match(value.split(":", 1)[1]))


# ── Mac: reading a batch ──
def complete_batches(d: B.Dest, fam: Family | None = None) -> list[str]:
    ids = set()
    for key in d.list_keys((fam or MC).export_prefix):
        parts = key.split("/")
        if len(parts) == 4 and parts[3] == B.COMPLETE and B.ATTEMPT.match(parts[2]):
            ids.add(parts[2])
    return sorted(ids)


def fetch_chain(d: B.Dest, batch_id: str, scratch: Path, instance: str, fam: Family | None = None) -> dict:
    """COMPLETE -> manifest, each hash checked against the one before; returns the manifest. Raises a fixed code."""
    fam = fam or MC
    folder = f"{fam.export_prefix}/{batch_id}"
    cpath, mpath = scratch / B.COMPLETE, scratch / B.MANIFEST
    d.get(f"{folder}/{B.COMPLETE}", cpath)
    comp = BT._json(cpath, "complete_unreadable")
    d.get(f"{folder}/{B.MANIFEST}", mpath)
    if B.sha256_file(mpath) != comp.get("manifest_sha256"):
        raise BackupError("manifest_mismatch")
    m = BT._json(mpath, "manifest_unreadable")
    try:
        if m.get("format") != fam.format or comp.get("format") != fam.format or m["batch_id"] != batch_id or comp["batch_id"] != batch_id \
                or m.get("instance") != instance:
            raise BackupError("manifest_format")
        if comp["objects"]["batch"] != {"sha256": m["objects"]["batch"]["sha256"], "bytes": m["objects"]["batch"]["bytes"]}:
            raise BackupError("manifest_mismatch")
        for name, sha, size in m["files"]:
            if not name_ok(str(name), instance, fam) or not V.HEX64.match(str(sha)) or isinstance(size, bool) \
                    or not isinstance(size, int) or not 0 <= size <= fam.max_file_bytes:
                raise BackupError("manifest_format")
        int(m["objects"]["batch"]["plaintext_bytes"])
    except (KeyError, TypeError, ValueError):
        raise BackupError("manifest_format") from None
    return m


def fetch_ciphertext(d: B.Dest, batch_id: str, m: dict, local: Path, fam: Family | None = None) -> None:
    d.get(f"{(fam or MC).export_prefix}/{batch_id}/{BATCH_OBJECT}", local)
    want = m["objects"]["batch"]
    if local.stat().st_size != int(want["bytes"]) or B.sha256_file(local) != want["sha256"]:
        raise BackupError("ciphertext_mismatch")


def extract(archive: Path, target: Path, m: dict, fam: Family | None = None) -> dict:
    """See ``_extract``. A stream that is not a whole gzip tar (truncated, corrupt) is ``archive_unsafe`` (a fixed integrity code), never a
    raw exception that would fail every run without a verdict. A local fault while writing the output (full disk, permission) is the
    TRANSIENT ``extract_write_failed``: the same bytes may well extract next run, so the batch must not be rejected for it."""
    try:
        return _extract(archive, target, m) if fam is None else _extract(archive, target, m, fam)
    except (tarfile.TarError, EOFError, zlib.error, gzip.BadGzipFile):
        raise BackupError("archive_unsafe") from None


def _extract(archive: Path, target: Path, m: dict, fam: Family | None = None) -> dict:
    """Strict: ``BATCH.json`` first and equal to the manifest's files; then exactly those files, each at its declared size, nothing
    else (no links, folders or duplicates), so the bytes written are bounded by the manifest. Returns ``BATCH.json``.
    Raises ``archive_unsafe`` / ``batch_doc_unreadable`` / ``batch_doc_mismatch``, or the transient ``extract_write_failed``.

    Output errors are split by what they mean: a member that already exists (``FileExistsError``: a duplicate), a file where a folder must
    be (``NotADirectoryError``) or a link where a file must be (``ELOOP`` under ``O_NOFOLLOW``) is the archive's doing, ``archive_unsafe``;
    any other ``OSError`` creating or writing the output is the Mac's (``ENOSPC``, ``EACCES``, ...), ``extract_write_failed``. Reading the
    archive stays separate so a corrupt stream is never mistaken for a write fault."""
    try:
        target.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise _write_error(exc) from None
    declared, seen, doc = {}, set(), None
    with tarfile.open(archive, "r:gz") as tar:
        for i, member in enumerate(tar):
            name = member.name
            if not member.isfile() or name in seen or "/" in name:
                raise BackupError("archive_unsafe")
            seen.add(name)
            if i == 0:
                if name != BATCH_DOC or member.size > MAX_DOC_BYTES:
                    raise BackupError("archive_unsafe")
                try:
                    doc = json.loads(tar.extractfile(member).read().decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    raise BackupError("batch_doc_unreadable") from None
                if not isinstance(doc, dict) or doc.get("format") != (fam or MC).format or doc.get("batch_id") != m["batch_id"] \
                        or doc.get("instance") != m["instance"] or sorted(doc.get("files") or []) != m["files"]:
                    raise BackupError("batch_doc_mismatch")
                declared = {n: z for n, _s, z in m["files"]}
                continue
            if declared.get(name) != member.size:
                raise BackupError("archive_unsafe")
            src = tar.extractfile(member)
            try:
                fd = os.open(str(target / name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            except OSError as exc:
                raise _write_error(exc) from None
            out, written = os.fdopen(fd, "wb"), False
            try:
                while True:
                    block = src.read(1 << 20)                   # read-side errors propagate to extract (archive_unsafe)
                    if not block:
                        break
                    try:
                        out.write(block)
                    except OSError as exc:
                        raise _write_error(exc) from None
                written = True
            finally:
                try:
                    out.close()                                 # the buffered tail is written here
                except OSError as exc:
                    if written:                                 # else the error already on its way out wins
                        raise _write_error(exc) from None
    if doc is None:
        raise BackupError("batch_doc_unreadable")
    return doc


def _write_error(exc: OSError) -> BackupError:
    if isinstance(exc, (FileExistsError, NotADirectoryError)) or exc.errno == errno.ELOOP:
        return BackupError("archive_unsafe")
    return BackupError("extract_write_failed")


def check_file(extracted: Path, name: str, sha: str, size: int, fam: Family | None = None) -> str | None:
    """``None`` when one extracted file is present, exactly the declared bytes, and the export shape for its own name; else a code."""
    fam = fam or MC
    path = extracted / name
    if not path.is_file() or path.is_symlink():
        return "file_missing"
    if path.stat().st_size != size:
        return "file_size_mismatch"
    if V.sha256_file(path) != sha:
        return "file_hash_mismatch"
    m = fam.file.match(name)
    try:
        doc = json.loads(path.read_text("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "not_an_export"
    if not fam.doc_ok(doc, m.group(1), m.group(2)):
        return "not_an_export"
    return None


def ack_doc(batch_id: str, files: list, at: datetime, fam: Family | None = None) -> dict:
    """``files`` = [[name, sha256, status]]."""
    return {"format": (fam or MC).ack_format, "batch_id": batch_id, "acked_utc": at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "files": sorted([n, s, st] for n, s, st in files)}
