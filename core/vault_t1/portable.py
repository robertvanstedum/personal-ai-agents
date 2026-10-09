"""Export, verify and restore: carrying the record out, and proving it came back whole (v0.6 section 1A items 2, 3, 7, 8)."""
from __future__ import annotations

import json
import os
import re
import shutil
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


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _empty_or_new(target: str) -> Path:
    path = Path(target)
    if path.is_symlink():
        raise VaultError("target_is_a_link")
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise VaultError("target_not_empty")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _file_entry(rel: str, data: bytes) -> dict:
    return {"path": rel, "bytes": len(data), "sha256": sha256(data)}


def export(vault: Vault, target: str, *, topic: str | None = None) -> dict:
    """Write a topic (or everything this caller may read) to a new folder. Returns the manifest."""
    members = vault.topic_members(topic) if topic else vault.records()
    if topic and not members:
        raise NotFound("topic_not_found")
    out = _empty_or_new(target)
    files, records, index_lines, terms = [], [], [], {}
    for rec in members:
        base = f"records/{rec.stem}"
        entry = {"id": rec.id, "dir": rec.kind_dir, "name": rec.stem, "source": rec.meta.get("source"), "chair": rec.meta.get("chair"),
                 "created": rec.meta.get("created"), "tier": rec.meta.get("tier"), "scope": rec.meta.get("scope"),
                 "edition": rec.meta.get("edition"), "record_file": f"{base}/{rec.stem}.md", "editions": []}
        _write(out / entry["record_file"], rec.raw)
        files.append(_file_entry(entry["record_file"], rec.raw))
        for edition in rec.editions:
            data = vault.edition_bytes(rec, edition)
            rel = f"{base}/editions/{edition.name}"
            _write(out / rel, data)
            files.append(_file_entry(rel, data))
            entry["editions"].append({"number": edition.number, "path": rel, "sha256": sha256(data), "bytes": len(data)})
            if edition.number == rec.meta.get("edition"):
                _, rows = reader.parse_edition(data)
                for t in reader.turns_of(rows):
                    text = str(t.get("text", ""))
                    who = (t.get("attrs") or {}).get("who")
                    index_lines.append(json.dumps({"record": rec.id, "edition": edition.number, "ordinal": t["ordinal"], "speaker": t.get("speaker"),
                                                   "who": who, "sha256": t.get("sha256"), "chars": len(text)}, sort_keys=True))
                    for word in set(w.lower() for w in TERM.findall(text)):
                        posting = terms.setdefault(word, [])
                        if len(posting) < MAX_POSTINGS:
                            posting.append(f"{rec.id}:{t['ordinal']}")
        records.append(entry)
    index = ("\n".join(index_lines) + ("\n" if index_lines else "")).encode()
    term_doc = (json.dumps({k: terms[k] for k in sorted(terms)}, sort_keys=True, ensure_ascii=False) + "\n").encode()
    schema = SCHEMA_DOC.encode()
    for name, data in ((INDEX, index), (TERMS, term_doc), (SCHEMA, schema)):
        _write(out / name, data)
        files.append(_file_entry(name, data))
    manifest = {"format": EXPORT_FORMAT, "tool_version": TOOL_VERSION, "selection": {"topic": topic, "scope": "robert" + "".join(f"+mandate:{m}" for m in vault.mandates)},
                "records": records, "files": sorted(files, key=lambda f: f["path"]),
                "counts": {"records": len(records), "editions": sum(len(r["editions"]) for r in records), "turns_indexed": len(index_lines)},
                "left_out": {"outside_scope": vault.withheld(), "outside_topic": (len(vault.records()) - len(members)) if topic else 0},
                "schema": SCHEMA, "index": INDEX, "terms": TERMS}
    raw = (json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode()
    _write(out / MANIFEST, raw)
    _write(out / MANIFEST_SHA, (sha256(raw) + "  " + MANIFEST + "\n").encode())
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


def verify_export(folder: str) -> dict:
    base = Path(folder)
    problems, checked = [], {"files": 0}
    try:
        raw = reader.read_file(base / MANIFEST, 64 * 1024 * 1024)
        side = reader.read_file(base / MANIFEST_SHA, 4096).decode().split()
    except VaultError as exc:
        return {"kind": "export", "ok": False, "checked": checked, "problems": [{"where": MANIFEST, "code": exc.code}]}
    if not side or side[0] != sha256(raw):
        problems.append({"where": MANIFEST, "code": "manifest_hash_mismatch"})
    try:
        manifest = json.loads(raw)
    except ValueError:
        return {"kind": "export", "ok": False, "checked": checked, "problems": problems + [{"where": MANIFEST, "code": "manifest_not_json"}]}
    if manifest.get("format") != EXPORT_FORMAT:
        problems.append({"where": MANIFEST, "code": "unknown_export_format"})
        return {"kind": "export", "ok": False, "checked": checked, "problems": problems}
    listed = set()
    for entry in manifest.get("files", []):
        rel = entry.get("path", "")
        if not rel or rel.startswith("/") or ".." in Path(rel).parts:
            problems.append({"where": rel or "?", "code": "unsafe_path_in_manifest"})
            continue
        listed.add(rel)
        try:
            data = reader.read_file(base / rel, reader.MAX_EDITION_BYTES)
        except VaultError as exc:
            problems.append({"where": rel, "code": exc.code})
            continue
        checked["files"] += 1
        if len(data) != entry.get("bytes") or sha256(data) != entry.get("sha256"):
            problems.append({"where": rel, "code": "file_hash_mismatch"})
        elif "/editions/" in rel:
            _check_edition(data, Path(rel).name, problems, rel)
    present = {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file() or p.is_symlink()} - {MANIFEST, MANIFEST_SHA}
    for extra in sorted(present - listed):
        problems.append({"where": extra, "code": "file_not_in_manifest"})
    return {"kind": "export", "ok": not problems, "checked": checked, "problems": problems, "counts": manifest.get("counts")}


def verify(path: str) -> dict:
    if (Path(path) / MANIFEST).exists():
        return verify_export(path)
    return verify_shelf(path)


# ── restore ────────────────────────────────────────────────────────────────────────────────────────────────────────
def restore(folder: str, target: str) -> dict:
    """Rebuild a shelf-shaped folder from a verified export. Nothing is written unless the export verifies first; the target must
    be absent or empty; every file is copied verbatim, so every ID, edition and hash is exactly what the export recorded."""
    report = verify_export(folder)
    if not report["ok"]:
        raise Damaged("export_does_not_verify")
    manifest = json.loads((Path(folder) / MANIFEST).read_bytes())
    out = _empty_or_new(target)
    written = 0
    for rec in manifest["records"]:
        stem, kind = rec["name"], rec["dir"]
        if kind not in reader.RECORD_DIRS or "/" in stem or stem in ("", ".", ".."):
            shutil.rmtree(out, ignore_errors=True)
            raise Damaged("unsafe_record_in_manifest")
        _write(out / kind / stem / f"{stem}.md", (Path(folder) / rec["record_file"]).read_bytes())
        written += 1
        for edition in rec["editions"]:
            _write(out / kind / stem / "editions" / Path(edition["path"]).name, (Path(folder) / edition["path"]).read_bytes())
    check = verify_shelf(str(out))
    if not check["ok"]:
        raise Damaged("restored_shelf_does_not_verify")
    return {"restored_records": written, "target_verified": True, "checked": check["checked"]}
