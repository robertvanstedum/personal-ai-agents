"""Export, verify and restore: carrying the record out, and proving it came back whole (v0.6 section 1A items 2, 3, 7, 8)."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from core.vault_t1 import reader
from core.vault_t1.reader import Damaged, NotFound, Vault, VaultError, sha256

EXPORT_FORMAT = "vault-export/1"
TOOL_VERSION = 1
MANIFEST, MANIFEST_SHA, SCHEMA, INDEX, TERMS = "manifest.json", "manifest.sha256", "SCHEMA.md", "index.jsonl", "terms.json"
TERM = re.compile(r"[^\W_]{3,40}", re.UNICODE)
MAX_POSTINGS = 200

SCHEMA_DOC = """# Vault export, format `vault-export/1`

This folder is a self-contained, owner-readable copy of part of a conversation record. It needs no MiniMoi software, no database,
no network and no account to read: everything is UTF-8 text (Markdown, JSON, JSONL).

## Files

| File | What it is |
|---|---|
| `manifest.json` | The list of everything below with sizes and SHA-256 hashes, the selection used, and counts of what was left out. |
| `manifest.sha256` | The SHA-256 of `manifest.json`. It detects accidental damage; it is not a signature and does not prove who made the export. |
| `SCHEMA.md` | This page. |
| `index.jsonl` | One line per conversation turn of each record's current edition: record, edition, ordinal, speaker, who, sha256 of the text, length. |
| `terms.json` | A plain word index: lower-case word to a list of `"<record id>:<ordinal>"` (at most 200 per word). A convenience, never the record. |
| `records/<name>/<name>.md` | A record: YAML front matter (facts about the conversation) then the readable turns, each framed with its byte length and hash. |
| `records/<name>/editions/<n>--<sha12>.jsonl` | An immutable edition: line 1 is a header, then one JSON object per turn, note or reference. `<sha12>` is the first 12 hex characters of the file's SHA-256. |

## Record front matter (the keys that matter)

`id` (a 26-character ULID, stable forever), `kind`, `created` (UTC), `chair` (whose conversation it is), `source` (`provider:source id`),
`source_hash` (SHA-256 of the *original* the edition was made from; the original itself is not in this export), `scope`
(`robert`, or `mandate:<id>`), `tier` (`raw` or `curated`), `tags`, `edition` and `edition_hash` (the current edition and its SHA-256),
`events` (what happened to the record), `normalized` (counts: turns, turns by role, omitted parts, redacted turns, coverage).

## Edition lines

Header: `format`, `provider`, `source_id`, `source_sha256`, `source_bytes`, `redacted_turns`, `omitted`, optional `manifest`.
Turn: `ordinal`, `speaker` (`human`, `assistant`, `system` or `coordination`), `line` (position in the source), `basis`, `sha256` of `text`, `text`,
optional `attrs` (who spoke, sequence, links). Credentials and payment numbers were removed before the edition was stored, so a
retained edition is a scrubbed derivative of the provider's original, not a byte copy of it.

## Checking it

`vault verify <this folder>` recomputes every hash. By hand: the SHA-256 of any listed file must equal the value in `manifest.json`,
and the SHA-256 of an edition file starts with the 12 characters in its name.

## What is not here

Hidden or encrypted model reasoning, attachments that were kept only as pointers, records outside the selection or the caller's
scope (only a count is given), and any key or credential. "Saved" means a retained edition exists; "backed up" and "synced" are
separate claims that this export makes no statement about.
"""


_OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


class Publisher:
    """Writes a new folder tree without ever replacing or removing anything it did not create.

    The target is created here (``mkdir`` is atomic, so a racing creator makes one of us fail), or an existing *empty* real folder
    is adopted. Every file is written to a temporary name inside the folder it belongs to and then published with ``link``, which
    refuses to overwrite: if another writer put a file there first, that file survives and this export stops. Folders are walked by
    descriptor with no link followed. On failure only the files and empty folders this publisher created are removed."""

    def __init__(self, target: str):
        path = Path(target)
        self.created_files: list[tuple[int, str]] = []
        self.created_dirs: list[tuple[int, str]] = []
        self.root_created = False
        if path.is_symlink():
            raise VaultError("target_is_a_link")
        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True)
        try:
            os.mkdir(path, 0o700)
            self.root_created = True
        except FileExistsError:
            pass
        try:
            self.root_fd = os.open(path, _OPEN_DIR)
        except OSError:
            raise VaultError("target_not_a_folder") from None
        if not self.root_created and os.listdir(self.root_fd):
            os.close(self.root_fd)
            raise VaultError("target_not_empty")
        self.path = path
        self._fds = {(): self.root_fd}

    def _dir(self, parts: tuple[str, ...]) -> int:
        if parts in self._fds:
            return self._fds[parts]
        parent = self._dir(parts[:-1])
        try:
            os.mkdir(parts[-1], 0o700, dir_fd=parent)
            self.created_dirs.append((parent, parts[-1]))
        except FileExistsError:
            pass
        try:
            fd = os.open(parts[-1], _OPEN_DIR, dir_fd=parent)
        except OSError:
            raise VaultError("destination_not_a_folder") from None
        self._fds[parts] = fd
        return fd

    def put(self, rel: str, data: bytes) -> None:
        parts = tuple(rel.split("/"))
        if any(p in ("", ".", "..") or "\x00" in p for p in parts):
            raise VaultError("unsafe_path")
        dir_fd = self._dir(parts[:-1])
        tmp = f".{parts[-1]}.{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.link(tmp, parts[-1], src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
        except FileExistsError:
            raise VaultError("destination_taken", {"path": rel}) from None
        finally:
            os.unlink(tmp, dir_fd=dir_fd)
        self.created_files.append((dir_fd, parts[-1]))

    def abort(self) -> None:
        """Remove only what this publisher made: its files, then its folders if they are empty, then the target if it made it and it is empty."""
        for dir_fd, name in reversed(self.created_files):
            try:
                os.unlink(name, dir_fd=dir_fd)
            except OSError:
                pass
        for dir_fd, name in reversed(self.created_dirs):
            try:
                os.rmdir(name, dir_fd=dir_fd)
            except OSError:
                pass
        self.close()
        if self.root_created:
            try:
                os.rmdir(self.path)
            except OSError:
                pass

    def close(self) -> None:
        for fd in self._fds.values():
            try:
                os.close(fd)
            except OSError:
                pass
        self._fds = {}


def _file_entry(rel: str, data: bytes) -> dict:
    return {"path": rel, "bytes": len(data), "sha256": sha256(data)}


def export(vault: Vault, target: str, *, topic: str | None = None, allow_incomplete: bool = False) -> dict:
    """Write a topic (or everything this caller may read) to a new folder. Returns the manifest.

    A complete export means every record in the shelf was either exported, out of this caller's scope (counted), or out of the
    topic (counted). If any entry of the shelf could not be read, the export refuses (``source_incomplete``, with counts) unless
    ``allow_incomplete`` is set, in which case the manifest says ``complete: false`` and how many entries were skipped."""
    members = vault.topic_members(topic) if topic else vault.records()
    if topic and not members:
        raise NotFound("topic_not_found")
    skipped = dict(sorted(vault.skipped.items()))
    if skipped and not allow_incomplete:
        raise VaultError("source_incomplete", {"skipped": skipped})
    pub = Publisher(target)
    try:
        files, records, index_lines, terms = [], [], [], {}
        for rec in members:
            base = f"records/{rec.stem}"
            entry = {"id": rec.id, "dir": rec.kind_dir, "name": rec.stem, "source": rec.meta.get("source"), "chair": rec.meta.get("chair"),
                     "created": rec.meta.get("created"), "tier": rec.meta.get("tier"), "scope": rec.meta.get("scope"),
                     "edition": rec.meta.get("edition"), "record_file": f"{base}/{rec.stem}.md", "editions": []}
            pub.put(entry["record_file"], rec.raw)
            files.append(_file_entry(entry["record_file"], rec.raw))
            for edition in rec.editions:
                data = vault.edition_bytes(rec, edition)
                rel = f"{base}/editions/{edition.name}"
                pub.put(rel, data)
                files.append(_file_entry(rel, data))
                entry["editions"].append({"number": edition.number, "path": rel, "sha256": sha256(data), "bytes": len(data)})
                if edition.number == rec.meta.get("edition"):
                    _, rows = reader.parse_edition(data)
                    for t in reader.turns_of(rows):
                        text = str(t.get("text", ""))
                        index_lines.append(json.dumps({"record": rec.id, "edition": edition.number, "ordinal": t["ordinal"], "speaker": t.get("speaker"),
                                                       "who": (t.get("attrs") or {}).get("who"), "sha256": t.get("sha256"), "chars": len(text)}, sort_keys=True))
                        for word in set(w.lower() for w in TERM.findall(text)):
                            posting = terms.setdefault(word, [])
                            if len(posting) < MAX_POSTINGS:
                                posting.append(f"{rec.id}:{t['ordinal']}")
            records.append(entry)
        index = ("\n".join(index_lines) + ("\n" if index_lines else "")).encode()
        term_doc = (json.dumps({k: terms[k] for k in sorted(terms)}, sort_keys=True, ensure_ascii=False) + "\n").encode()
        for name, data in ((INDEX, index), (TERMS, term_doc), (SCHEMA, SCHEMA_DOC.encode())):
            pub.put(name, data)
            files.append(_file_entry(name, data))
        complete = not skipped
        manifest = {"format": EXPORT_FORMAT, "tool_version": TOOL_VERSION, "complete": complete,
                    "selection": {"topic": topic, "scope": "robert" + "".join(f"+mandate:{m}" for m in vault.mandates)},
                    "records": records, "files": sorted(files, key=lambda f: f["path"]),
                    "counts": {"records": len(records), "editions": sum(len(r["editions"]) for r in records), "turns_indexed": len(index_lines)},
                    "left_out": {"outside_scope": vault.withheld(), "outside_topic": (len(vault.records()) - len(members)) if topic else 0,
                                 "skipped_unreadable": skipped},
                    "limitations": [] if complete else ["some shelf entries could not be read and are not in this export; see left_out.skipped_unreadable"],
                    "schema": SCHEMA, "index": INDEX, "terms": TERMS}
        raw = (json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode()
        pub.put(MANIFEST, raw)
        pub.put(MANIFEST_SHA, (sha256(raw) + "  " + MANIFEST + "\n").encode())
    except BaseException:
        pub.abort()
        raise
    pub.close()
    return manifest


# ── verify ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def _check_edition(data: bytes, name: str, problems: list, where: str) -> None:
    m = reader.EDITION_NAME.match(name)
    if not m or sha256(data)[:12] != m.group(2):
        problems.append({"where": where, "code": "edition_hash_mismatch"})
        return
    try:
        header, rows = reader.parse_edition(data)
    except VaultError as exc:
        problems.append({"where": where, "code": exc.code})
        return
    expected = 1
    for t in reader.turns_of(rows):
        if t["ordinal"] != expected:
            problems.append({"where": where, "code": "turn_order_broken"})
            break
        expected += 1
        if t.get("sha256") != sha256(str(t.get("text", "")).encode("utf-8")):
            problems.append({"where": where, "code": "turn_hash_mismatch", "ordinal": t["ordinal"]})
    if not header.get("source_sha256"):
        problems.append({"where": where, "code": "edition_without_source_hash"})


def verify_shelf(root: str) -> dict:
    """Every record's front matter, every edition's name and hash, the current edition against the record, every turn's hash."""
    vault = Vault(root, mandates=())
    problems, checked = [], {"records": 0, "editions": 0, "turns": 0}
    for rec in vault._all():                                   # verification looks at everything, whatever the reading scope
        checked["records"] += 1
        names = [e.number for e in rec.editions]
        if names != list(range(1, len(names) + 1)):
            problems.append({"where": rec.id, "code": "edition_numbers_not_contiguous"})
        current = rec.current()
        if current is None:
            problems.append({"where": rec.id, "code": "current_edition_missing"})
        for edition in rec.editions:
            try:
                data = reader.read_file(edition.path, reader.MAX_EDITION_BYTES)
            except VaultError as exc:
                problems.append({"where": f"{rec.id}#{edition.number}", "code": exc.code})
                continue
            checked["editions"] += 1
            _check_edition(data, edition.name, problems, f"{rec.id}#{edition.number}")
            if edition is current:
                if rec.meta.get("edition_hash") not in (None, sha256(data)):
                    problems.append({"where": f"{rec.id}#{edition.number}", "code": "current_edition_differs_from_record"})
                try:
                    header, rows = reader.parse_edition(data)
                    checked["turns"] += len(reader.turns_of(rows))
                    if header.get("source_sha256") != rec.meta.get("source_hash"):
                        problems.append({"where": f"{rec.id}#{edition.number}", "code": "source_hash_differs_from_record"})
                except VaultError:
                    pass
    if vault.unreadable:
        problems.append({"where": "shelf", "code": "unreadable_records", "count": vault.unreadable})
    return {"kind": "shelf", "ok": not problems, "checked": checked, "problems": problems}


NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,150}$")
TOP_FILES = (SCHEMA, INDEX, TERMS)


def _load_export(folder: str) -> tuple[dict | None, dict[str, bytes], list]:
    """(manifest, {path: bytes}, problems): the whole manifest graph checked, and every byte read exactly once, safely.

    Nothing the manifest points at is read unless it is a listed file with a normalized relative path; every record and edition
    pointer must name a listed file whose size and hash match; names, IDs and edition numbers must be consistent."""
    problems, blobs = [], {}
    try:
        raw = reader.read_path(folder, [MANIFEST], 64 * 1024 * 1024)
        side = reader.read_path(folder, [MANIFEST_SHA], 4096).decode().split()
    except VaultError as exc:
        return None, blobs, [{"where": MANIFEST, "code": exc.code}]
    if not side or side[0] != sha256(raw):
        problems.append({"where": MANIFEST, "code": "manifest_hash_mismatch"})
    try:
        manifest = json.loads(raw)
    except ValueError:
        return None, blobs, problems + [{"where": MANIFEST, "code": "manifest_not_json"}]
    if not isinstance(manifest, dict) or manifest.get("format") != EXPORT_FORMAT:
        return None, blobs, problems + [{"where": MANIFEST, "code": "unknown_export_format"}]
    listed = {}
    for entry in manifest.get("files") or []:
        rel = entry.get("path") if isinstance(entry, dict) else None
        parts = rel.split("/") if isinstance(rel, str) else []
        if not parts or any(p in ("", ".", "..") or "\\" in p or "\x00" in p for p in parts) or "/".join(parts) != rel:
            problems.append({"where": str(rel)[:80], "code": "unsafe_path_in_manifest"})
            continue
        if rel in listed:
            problems.append({"where": rel, "code": "duplicate_path_in_manifest"})
            continue
        if not (rel in TOP_FILES or (parts[0] == "records" and len(parts) >= 3)):
            problems.append({"where": rel, "code": "path_outside_the_export_layout"})
            continue
        listed[rel] = entry
        try:
            data = reader.read_path(folder, parts, reader.MAX_EDITION_BYTES)
        except VaultError as exc:
            problems.append({"where": rel, "code": exc.code})
            continue
        if len(data) != entry.get("bytes") or sha256(data) != entry.get("sha256"):
            problems.append({"where": rel, "code": "file_hash_mismatch"})
            continue
        blobs[rel] = data
    # the records graph: every pointer is a verified listed file, in exactly the layout the exporter writes
    seen_ids, seen_names = set(), set()
    for rec in manifest.get("records") or []:
        if not isinstance(rec, dict):
            problems.append({"where": "records", "code": "record_entry_malformed"})
            continue
        name, rid = rec.get("name"), rec.get("id")
        if not isinstance(name, str) or not NAME_OK.match(name) or rec.get("dir") not in reader.RECORD_DIRS or not reader.ULID.match(str(rid)):
            problems.append({"where": str(name)[:80], "code": "record_entry_malformed"})
            continue
        if rid in seen_ids or name in seen_names:
            problems.append({"where": name, "code": "duplicate_record_in_manifest"})
            continue
        seen_ids.add(rid)
        seen_names.add(name)
        if rec.get("record_file") != f"records/{name}/{name}.md" or rec["record_file"] not in blobs:
            problems.append({"where": name, "code": "record_pointer_not_a_verified_listed_file"})
            continue
        try:
            meta, _ = reader.split_record(blobs[rec["record_file"]].decode("utf-8"))
        except (VaultError, UnicodeDecodeError):
            problems.append({"where": name, "code": "record_file_unreadable"})
            continue
        if meta.get("id") != rid:
            problems.append({"where": name, "code": "record_id_differs_from_manifest"})
        numbers = []
        for ed in rec.get("editions") or []:
            path = ed.get("path") if isinstance(ed, dict) else None
            m = reader.EDITION_NAME.match(Path(str(path)).name)
            if (not m or path != f"records/{name}/editions/{m.group(0)}" or int(m.group(1)) != ed.get("number") or path not in blobs
                    or sha256(blobs[path]) != ed.get("sha256") or m.group(2) != ed["sha256"][:12]):
                problems.append({"where": f"{name}#{ed.get('number') if isinstance(ed, dict) else '?'}", "code": "edition_pointer_not_a_verified_listed_file"})
                continue
            numbers.append(ed["number"])
            _check_edition(blobs[path], m.group(0), problems, path)
        if numbers != list(range(1, len(numbers) + 1)):
            problems.append({"where": name, "code": "edition_numbers_not_contiguous"})
        if meta.get("edition") not in numbers:
            problems.append({"where": name, "code": "current_edition_not_in_the_export"})
    # nothing in the folder that the manifest does not list
    try:
        present = _walk(folder)
    except VaultError as exc:
        problems.append({"where": "export", "code": exc.code})
        present = set()
    for extra in sorted(present - set(listed) - {MANIFEST, MANIFEST_SHA}):
        problems.append({"where": extra, "code": "file_not_in_manifest"})
    return manifest, blobs, problems


def _is_folder(folder: str, parts: list[str]) -> bool:
    try:
        os.close(reader._open_chain(folder, parts, final_file=False))
        return True
    except VaultError:
        return False


def _walk(folder: str) -> set[str]:
    """Every file, link or special entry under the export folder, by descriptor walk with no link followed."""
    out: set[str] = set()

    def walk(parts: list[str]) -> None:
        for name, plain in reader.list_dir(folder, parts) or []:
            rel = [*parts, name]
            if plain and _is_folder(folder, rel):
                walk(rel)
            else:
                out.add("/".join(rel))
    walk([])
    return out


def verify_export(folder: str) -> dict:
    manifest, blobs, problems = _load_export(folder)
    result = {"kind": "export", "ok": not problems, "checked": {"files": len(blobs)}, "problems": problems}
    if manifest:
        result["counts"] = manifest.get("counts")
        result["complete"] = manifest.get("complete")
        result["limitations"] = manifest.get("limitations") or []
    return result


def verify(path: str) -> dict:
    if (Path(path) / MANIFEST).exists():
        return verify_export(path)
    return verify_shelf(path)


# ── restore ────────────────────────────────────────────────────────────────────────────────────────────────────────
def restore(folder: str, target: str) -> dict:
    """Rebuild a shelf-shaped folder from an export. The whole manifest graph is verified first and restore writes *only those
    verified bytes* (never a second read), so a pointer cannot lead it anywhere else. Nothing is created if verification fails; the
    target must be absent or an empty real folder; files are published without replacing anything."""
    manifest, blobs, problems = _load_export(folder)
    if problems or manifest is None:
        raise Damaged("export_does_not_verify", {"problems": len(problems)})
    pub = Publisher(target)
    try:
        written = 0
        for rec in manifest["records"]:
            name, kind = rec["name"], rec["dir"]
            pub.put(f"{kind}/{name}/{name}.md", blobs[rec["record_file"]])
            written += 1
            for edition in rec["editions"]:
                pub.put(f"{kind}/{name}/editions/{Path(edition['path']).name}", blobs[edition["path"]])
        check = verify_shelf(str(pub.path))
        if not check["ok"]:
            raise Damaged("restored_shelf_does_not_verify")
    except BaseException:
        pub.abort()
        raise
    pub.close()
    return {"restored_records": written, "target_verified": True, "checked": check["checked"], "complete": manifest.get("complete")}
