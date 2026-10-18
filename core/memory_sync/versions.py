"""Record versions: the unit the incremental sync moves, and the facts both hosts compute the same way (17 Oct 2026).

A **record version** is one record folder ``<kind>/<name>/`` (``<name>.md`` plus ``editions/*``) as it stands now: the record id and
edition number and hash from the main file's front matter, and the sha256 and size of every file in the folder. Its ``digest`` is the
sha256 of the folder path and that file list, so two hosts hold the same version exactly when their digests agree. The main file can
change without a new edition (an owner designation appends an event), and the digest catches that too.

Both the Mac (``vault-sync``) and EC2 (``vault-apply``) build their inventory with ``record_versions``, so "production holds it" and "the
Mac has it" are the same computation on two shelves. Only record folders travel: the append-only ledger, run status, the outbox, assets
and evaluations are not record versions (production serves records; those reach it only through a full snapshot reseed).

What leaves a host in plain text (inventory, acknowledgements, a batch's outer manifest) holds record ids, edition numbers and hashes,
counts and key-set digests only: never a folder name, title, path or content. Folder names (title slugs) travel only inside the
encrypted batch.

``HashCache`` keys each file by (size, mtime_ns, inode), so a run with no changes is one directory walk and no hashing.
Stdlib plus the vault reader's front-matter parser (PyYAML): this module is part of the EC2 tools bundle.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from core.vault_t1.reader import RECORD_DIRS, ULID, Damaged, split_record

BATCH_FORMAT = "vault-sync-batch/1"
ACK_FORMAT = "vault-sync-ack/1"
INVENTORY_FORMAT = "vault-sync-inventory/1"
BATCH_PREFIX = "sync/batches"                   # <dest>/sync/batches/<batch_id>/{batch.tar.age, manifest.json, COMPLETE.json}
ACK_PREFIX = "sync/acks"                        # <dest>/sync/acks/<batch_id>.json and <dest>/sync/acks/inventory.json
INVENTORY_NAME = "inventory.json"
BATCH_OBJECT = "batch.tar.age"
BATCH_DOC = "BATCH.json"                        # first member of the (encrypted) batch tar: folder names, file lists, source-index entries
RECORDS_ROOT = "records"                        # batch tar members: records/<kind>/<name>/<file>
STORED, SUPERSEDED, REJECTED = "stored", "superseded", "rejected"
CLEARED = (STORED, SUPERSEDED)                  # an acknowledgement that production holds this version or a newer one
SKIP = re.compile(r"(^|/)(\.[^/]*\.tmp-[^/]*|[^/]*\.lock|\.DS_Store)$")     # the snapshot tar's own skip rule
CODE = re.compile(r"^[a-z0-9][a-z0-9_]{0,39}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
FRONT_MATTER_LIMIT = 1 << 20


@dataclass(frozen=True)
class Version:
    record_id: str
    kind: str
    name: str
    edition: int
    edition_hash: str
    source_key: str | None
    files: tuple                                # ((rel, sha256, size), ...) sorted by rel; rel is relative to the record folder
    digest: str

    @property
    def dir(self) -> str:
        return f"{self.kind}/{self.name}"

    @property
    def bytes(self) -> int:
        return sum(size for _, _, size in self.files)

    def key(self) -> dict:
        """What may travel in plain text: id, edition, hashes."""
        return {"record_id": self.record_id, "edition": self.edition, "edition_hash": self.edition_hash, "digest": self.digest}


def version_digest(kind: str, name: str, files) -> str:
    doc = {"dir": f"{kind}/{name}", "files": [[rel, sha, int(size)] for rel, sha, size in sorted(files)]}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def version_set_digest(entries: dict) -> str:
    """One digest over every (record id, edition hash, version digest) a host holds: equal digests mean an identical version set."""
    lines = sorted(f"{rid} {e['edition_hash']} {e['digest']}" for rid, e in entries.items())
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


class HashCache:
    """rel -> [size, mtime_ns, inode, sha256]. Loaded leniently (a bad cache is an empty cache), saved whole, 0600."""

    def __init__(self, path: Path | None = None):
        self.path, self.entries, self.hits, self.misses = (Path(path) if path else None), {}, 0, 0
        if self.path and self.path.is_file() and not self.path.is_symlink():
            try:
                doc = json.loads(self.path.read_text("utf-8"))
                if isinstance(doc, dict) and isinstance(doc.get("files"), dict):
                    self.entries = {k: v for k, v in doc["files"].items() if isinstance(v, list) and len(v) == 4}
            except (OSError, ValueError):
                self.entries = {}
        self._seen: set[str] = set()

    def sha(self, rel: str, path: Path, st: os.stat_result) -> str:
        self._seen.add(rel)
        hit = self.entries.get(rel)
        if hit and hit[0] == st.st_size and hit[1] == st.st_mtime_ns and hit[2] == st.st_ino and HEX64.match(str(hit[3])):
            self.hits += 1
            return hit[3]
        self.misses += 1
        sha = sha256_file(path)
        self.entries[rel] = [st.st_size, st.st_mtime_ns, st.st_ino, sha]
        return sha

    def save(self) -> None:
        if not self.path:
            return
        keep = {k: v for k, v in self.entries.items() if k in self._seen}
        write_json_atomic(self.path, {"files": keep})


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_front_matter(path: Path) -> dict:
    """The main file's front matter only (a record body can be large). Raises ``Damaged``."""
    buf = b""
    with open(path, "rb") as f:
        while len(buf) < FRONT_MATTER_LIMIT:
            chunk = f.read(1 << 16)
            if not chunk:
                break
            buf += chunk
            end = buf.find(b"\n---\n", 4)
            if end != -1:
                buf = buf[:end + 5]
                break
    try:
        meta, _ = split_record(buf.decode("utf-8"))
    except UnicodeDecodeError:
        raise Damaged("front_matter_not_utf8") from None
    return meta


def _folder_files(folder: Path, root: Path, cache: HashCache | None) -> tuple | None:
    """Every regular file under one record folder as (rel, sha256, size); ``None`` when the folder holds a link or special file."""
    out = []
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        dirnames.sort()
        for d in dirnames:
            if os.path.islink(os.path.join(dirpath, d)):
                return None
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(folder).as_posix()
            if SKIP.search(rel):
                continue
            st = os.lstat(path)
            if not (st.st_mode & 0o170000 == 0o100000):         # regular files only
                return None
            sha = cache.sha(path.relative_to(root).as_posix(), path, st) if cache else sha256_file(path)
            out.append((rel, sha, st.st_size))
    return tuple(sorted(out))


def record_version(root: Path, kind: str, name: str, cache: HashCache | None = None) -> Version:
    """One record folder's version. Raises ``Damaged`` with a fixed code when the folder is not a syncable record."""
    folder = root / kind / name
    if folder.is_symlink() or not folder.is_dir():
        raise Damaged("entry_not_a_folder")
    main = folder / f"{name}.md"
    if main.is_symlink() or not main.is_file():
        raise Damaged("folder_without_record_file")
    files = _folder_files(folder, root, cache)
    if files is None:
        raise Damaged("record_holds_a_link_or_special_file")
    meta = read_front_matter(main)
    rid, edition, edition_hash = str(meta.get("id", "")), meta.get("edition"), str(meta.get("edition_hash") or "")
    if not ULID.match(rid):
        raise Damaged("record_without_a_valid_id")
    if isinstance(edition, bool) or not isinstance(edition, int) or edition < 1 or not HEX64.match(edition_hash):
        raise Damaged("record_without_an_edition")
    source = meta.get("source")
    return Version(rid, kind, name, edition, edition_hash, str(source) if source else None, files, version_digest(kind, name, files))


def record_versions(shelf_root, cache: HashCache | None = None) -> tuple[dict, Counter]:
    """record id -> Version for every record folder on the shelf, plus a Counter of folders that are not syncable records (fixed codes)."""
    root = Path(shelf_root)
    versions: dict[str, Version] = {}
    problems: Counter = Counter()
    for kind in RECORD_DIRS:
        base = root / kind
        if base.is_symlink():
            problems["record_folder_not_a_folder"] += 1
            continue
        if not base.is_dir():
            continue
        for name in sorted(os.listdir(base)):
            if name.startswith("."):
                continue
            try:
                v = record_version(root, kind, name, cache)
            except Damaged as exc:
                problems[str(exc.args[0]) if exc.args else "record_unreadable"] += 1
                continue
            except (OSError, UnicodeDecodeError):
                problems["record_unreadable"] += 1
                continue
            if v.record_id in versions:
                problems["duplicate_record_id"] += 1
                continue
            versions[v.record_id] = v
    return versions, problems


def source_index(shelf_root) -> dict:
    """The shelf's own ``_ledger/source_index.json`` (``provider:id`` -> entry); empty when absent."""
    path = Path(shelf_root) / "_ledger" / "source_index.json"
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        raise Damaged("source_index_unreadable")
    try:
        doc = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        raise Damaged("source_index_unreadable") from None
    if not isinstance(doc, dict):
        raise Damaged("source_index_unreadable")
    return doc


def entries_of(versions: dict) -> dict:
    return {rid: {"edition": v.edition, "edition_hash": v.edition_hash, "digest": v.digest} for rid, v in versions.items()}


def inventory_doc(shelf_root, versions: dict, *, generated_utc: str, through_batch: str | None, release: str | None, problems=None) -> dict:
    """What production holds, as the Mac diffs against it: every (record id, edition, edition hash, digest), the version-set digest,
    and the shelf's source-key set digests (the same facts ``installed.json`` carries). Ids and hashes only."""
    from core.memory_backup.production import source_key_facts
    entries = entries_of(versions)
    return {"format": INVENTORY_FORMAT, "generated_utc": generated_utc, "through_batch": through_batch, "release": release,
            "count": len(entries), "version_set_digest": version_set_digest(entries), "records": entries,
            "source_keys": source_key_facts(shelf_root), "problems": dict(sorted((problems or {}).items()))}


def parse_inventory(doc) -> dict:
    """A production inventory -> record id -> entry. Raises ValueError("inventory_malformed") on anything unexpected."""
    if not isinstance(doc, dict) or doc.get("format") != INVENTORY_FORMAT or not isinstance(doc.get("records"), dict):
        raise ValueError("inventory_malformed")
    out = {}
    for rid, e in doc["records"].items():
        if not ULID.match(str(rid)) or not isinstance(e, dict) or isinstance(e.get("edition"), bool) or not isinstance(e.get("edition"), int) \
                or not HEX64.match(str(e.get("edition_hash", ""))) or not HEX64.match(str(e.get("digest", ""))):
            raise ValueError("inventory_malformed")
        out[rid] = {"edition": e["edition"], "edition_hash": e["edition_hash"], "digest": e["digest"]}
    return out


def write_json_atomic(path: Path, doc, mode: int = 0o600) -> None:
    """Whole document to a temp file beside ``path``, fsync, rename, fsync the folder. The file is ``mode``."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
    data = (json.dumps(doc, indent=1, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            if n <= 0:
                raise OSError("write made no progress")
            view = view[n:]
        os.fchmod(fd, mode)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        tmp.unlink(missing_ok=True)
        raise
    os.close(fd)
    os.replace(tmp, path)
    dfd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def read_json(path: Path, default=None):
    try:
        doc = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return default
    return doc
