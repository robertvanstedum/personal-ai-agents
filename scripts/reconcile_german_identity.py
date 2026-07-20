#!/usr/bin/env python3
"""Inventory and reconcile approved German identity records.

This utility has no production defaults and never discovers or assumes a user
ID itself.  The operator must pass the numeric ID discovered from the live auth
store, review the generated manifest, and supply that manifest's digest again
for apply.  Only Gespräche sessions and explicitly named Schreiben source files
can be changed; other identity-keyed stores are inventory-only.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


MANIFEST_VERSION = 1
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_.-]+$")


class MigrationRefused(RuntimeError):
    """Raised when reviewed state or a safety invariant does not match."""


def _canonical_json(data: Any) -> bytes:
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _read_json(path: Path, expected_type: type | tuple[type, ...] | None = None):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationRefused(f"invalid JSON or unreadable file: {path}") from exc
    if expected_type is not None and not isinstance(data, expected_type):
        raise MigrationRefused(f"unexpected JSON shape: {path}")
    return data


def _atomic_write_bytes(path: Path, payload: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".issue95-", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _atomic_write_json(path: Path, data: Any) -> None:
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    _atomic_write_bytes(path, payload, mode=mode)


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MigrationRefused(f"path escapes German data root: {path}") from exc


def _resolve_relative(root: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute():
        raise MigrationRefused(f"invalid manifest path: {relative!r}")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise MigrationRefused(f"manifest path escapes data root: {relative}") from exc
    return candidate


def _numeric_user_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _identity_state(record: dict, field: str) -> str:
    if field not in record:
        return "missing"
    value = record[field]
    if value is None:
        return "null"
    if isinstance(value, bool):
        return f"bool:{str(value).lower()}"
    if isinstance(value, int):
        return f"number:{value}"
    if isinstance(value, str):
        return f"string:{value}"
    return f"{type(value).__name__}"


def _value_state(value: Any, present: bool = True) -> str:
    return _identity_state({"value": value} if present else {}, "value")


def _validate_session_sources(sources: list[str]) -> list[str]:
    normalized = sorted(set(sources))
    for source in normalized:
        valid = source in {"missing", "null"}
        valid = valid or source.startswith("string:") and bool(source[7:])
        valid = valid or source.startswith("number:") and source[7:].isdigit()
        if not valid:
            raise MigrationRefused(f"unsupported session identity source: {source}")
    return normalized


def _validate_writing_key(key: str) -> str:
    if not key or not _SAFE_KEY.fullmatch(key) or key in {".", ".."}:
        raise MigrationRefused(f"unsafe writing source key: {key!r}")
    return key


def _group_values(values: list[Any], present: list[bool] | None = None) -> dict[str, int]:
    if present is None:
        present = [True] * len(values)
    counts = Counter(_value_state(value, is_present) for value, is_present in zip(values, present))
    return dict(sorted(counts.items()))


def inventory_identity_stores(data_root: str | Path) -> dict[str, Any]:
    """Return counts and identity shapes without including personal content."""
    root = Path(data_root).resolve()

    session_states: Counter[str] = Counter()
    session_dates: list[str] = []
    invalid_sessions: list[str] = []
    session_files = []
    for path in sorted((root / "sessions").glob("*.json")):
        try:
            record = _read_json(path, dict)
        except MigrationRefused:
            invalid_sessions.append(_relative_path(root, path))
            continue
        session_states[_identity_state(record, "user_id")] += 1
        if isinstance(record.get("date"), str):
            session_dates.append(record["date"])
        session_files.append({
            "path": _relative_path(root, path),
            "sha256": _sha256_file(path),
            "identity": _identity_state(record, "user_id"),
        })

    writing_files = []
    for path in sorted((root / "writing_sessions").glob("user_*.json")):
        try:
            data = _read_json(path, dict)
            entries = data.get("entries", [])
            if not isinstance(entries, list):
                raise MigrationRefused("entries must be a list")
            values = [entry.get("user_id") for entry in entries if isinstance(entry, dict)]
            present = ["user_id" in entry for entry in entries if isinstance(entry, dict)]
            writing_files.append({
                "path": _relative_path(root, path),
                "sha256": _sha256_file(path),
                "entry_count": len(entries),
                "identity_counts": _group_values(values, present),
                "valid": all(isinstance(entry, dict) for entry in entries),
            })
        except MigrationRefused:
            writing_files.append({
                "path": _relative_path(root, path),
                "valid": False,
            })

    note_files = []
    for path in sorted((root / "lesen_notes").glob("user_*.json")):
        try:
            data = _read_json(path, dict)
            notes = data.get("notes", [])
            if not isinstance(notes, list):
                raise MigrationRefused("notes must be a list")
            values = [note.get("user_id") for note in notes if isinstance(note, dict)]
            present = ["user_id" in note for note in notes if isinstance(note, dict)]
            note_files.append({
                "path": _relative_path(root, path),
                "sha256": _sha256_file(path),
                "note_count": len(notes),
                "identity_counts": _group_values(values, present),
                "valid": all(isinstance(note, dict) for note in notes),
            })
        except MigrationRefused:
            note_files.append({
                "path": _relative_path(root, path),
                "valid": False,
            })

    phrasebook_path = root / "config" / "phrasebook.json"
    phrasebook = {"present": phrasebook_path.exists(), "valid": True, "entry_count": 0}
    if phrasebook_path.exists():
        try:
            data = _read_json(phrasebook_path, dict)
            phrases = data.get("phrases", [])
            if not isinstance(phrases, list):
                raise MigrationRefused("phrases must be a list")
            values = [phrase.get("user") for phrase in phrases if isinstance(phrase, dict)]
            present = ["user" in phrase for phrase in phrases if isinstance(phrase, dict)]
            phrasebook.update({
                "sha256": _sha256_file(phrasebook_path),
                "entry_count": len(phrases),
                "identity_counts": _group_values(values, present),
                "valid": all(isinstance(phrase, dict) for phrase in phrases),
            })
        except MigrationRefused:
            phrasebook["valid"] = False

    persona_path = root / "config" / "persona_memory.json"
    persona_memory = {"present": persona_path.exists(), "valid": True, "entry_count": 0}
    if persona_path.exists():
        try:
            data = _read_json(persona_path, dict)
            values = []
            present = []
            for entry in data.values():
                if isinstance(entry, dict):
                    values.append(entry.get("user"))
                    present.append("user" in entry)
            persona_memory.update({
                "sha256": _sha256_file(persona_path),
                "entry_count": len(data),
                "identity_counts": _group_values(values, present),
                "valid": all(isinstance(entry, dict) for entry in data.values()),
            })
        except MigrationRefused:
            persona_memory["valid"] = False

    return {
        "sessions": {
            "file_count": sum(session_states.values()) + len(invalid_sessions),
            "identity_counts": dict(sorted(session_states.items())),
            "date_min": min(session_dates) if session_dates else None,
            "date_max": max(session_dates) if session_dates else None,
            "invalid_paths": invalid_sessions,
            "files": session_files,
        },
        "writing_sessions": {"files": writing_files},
        "lesen_notes": {"files": note_files},
        "phrasebook": phrasebook,
        "persona_memory": persona_memory,
    }


def _entry_for_compare(entry: dict, target_user_id: int) -> dict:
    normalized = copy.deepcopy(entry)
    normalized["user_id"] = target_user_id
    return normalized


def _logical_entry_fingerprint(entry: dict, target_user_id: int) -> str:
    normalized = _entry_for_compare(entry, target_user_id)
    normalized.pop("id", None)
    return _sha256_bytes(_canonical_json(normalized))


def _validated_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = _read_json(path, dict)
    entries = data.get("entries", [])
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise MigrationRefused(f"writing entries have unexpected shape: {path}")
    for entry in entries:
        if not isinstance(entry.get("id"), str) or not entry["id"]:
            raise MigrationRefused(f"writing entry missing stable id: {path}")
    return entries


def _merged_writing_entries(
    target_entries: list[dict], source_entries: list[list[dict]], target_user_id: int
) -> list[dict]:
    merged: list[dict] = []
    by_id: dict[str, dict] = {}
    fingerprint_to_id: dict[str, str] = {}

    for entry in [*target_entries, *(item for group in source_entries for item in group)]:
        normalized = _entry_for_compare(entry, target_user_id)
        entry_id = normalized["id"]
        if entry_id in by_id:
            if by_id[entry_id] != normalized:
                raise MigrationRefused(f"writing id collision: {entry_id}")
            continue
        fingerprint = _logical_entry_fingerprint(normalized, target_user_id)
        if fingerprint in fingerprint_to_id and fingerprint_to_id[fingerprint] != entry_id:
            raise MigrationRefused(
                "writing logical collision: "
                f"{fingerprint_to_id[fingerprint]} and {entry_id}"
            )
        by_id[entry_id] = normalized
        fingerprint_to_id[fingerprint] = entry_id
        merged.append(normalized)
    return merged


def manifest_digest(manifest: dict[str, Any]) -> str:
    unsigned = copy.deepcopy(manifest)
    unsigned.pop("digest", None)
    return _sha256_bytes(_canonical_json(unsigned))


def _backup_manifest_digest(metadata: dict[str, Any]) -> str:
    unsigned = copy.deepcopy(metadata)
    unsigned.pop("backup_digest", None)
    return _sha256_bytes(_canonical_json(unsigned))


def _assert_inventory_valid(inventory: dict[str, Any]) -> None:
    if inventory["sessions"]["invalid_paths"]:
        raise MigrationRefused("session inventory contains invalid JSON")
    invalid_writing = [
        item["path"]
        for item in inventory["writing_sessions"]["files"]
        if not item.get("valid")
    ]
    invalid_notes = [
        item["path"]
        for item in inventory["lesen_notes"]["files"]
        if not item.get("valid")
    ]
    if invalid_writing or invalid_notes:
        raise MigrationRefused("identity-keyed file inventory contains invalid JSON")
    if not inventory["phrasebook"].get("valid"):
        raise MigrationRefused("phrasebook inventory is invalid")
    if not inventory["persona_memory"].get("valid"):
        raise MigrationRefused("persona-memory inventory is invalid")


def build_manifest(
    data_root: str | Path,
    *,
    target_user_id: int,
    session_sources: list[str],
    writing_source_keys: list[str],
) -> dict[str, Any]:
    root = Path(data_root).resolve()
    if not root.is_dir():
        raise MigrationRefused(f"German data root does not exist: {root}")
    if not _numeric_user_id(target_user_id):
        raise MigrationRefused("target user ID must be a positive integer")

    sources = _validate_session_sources(session_sources)
    writing_keys = sorted({_validate_writing_key(key) for key in writing_source_keys})
    target_key = str(target_user_id)
    if target_key in writing_keys:
        raise MigrationRefused("target writing key cannot also be a source")

    session_changes = []
    for path in sorted((root / "sessions").glob("*.json")):
        record = _read_json(path, dict)
        state = _identity_state(record, "user_id")
        if state not in sources:
            continue
        session_changes.append({
            "path": _relative_path(root, path),
            "sha256_before": _sha256_file(path),
            "identity_before": state,
            "identity_after": f"number:{target_user_id}",
            "session_id": record.get("session_id", path.stem),
            "date": record.get("date"),
            "source": record.get("source"),
        })

    writing_dir = root / "writing_sessions"
    target_path = writing_dir / f"user_{target_key}.json"
    target_entries = _validated_entries(target_path)
    source_paths = []
    source_entries = []
    for key in writing_keys:
        path = writing_dir / f"user_{key}.json"
        if not path.exists():
            continue
        entries = _validated_entries(path)
        allowed_states = {"missing", "null", f"string:{key}", f"number:{target_user_id}"}
        unexpected_states = sorted({
            _identity_state(entry, "user_id")
            for entry in entries
            if _identity_state(entry, "user_id") not in allowed_states
        })
        if unexpected_states:
            raise MigrationRefused(
                f"writing source {path.name} contains unapproved identities: "
                + ", ".join(unexpected_states)
            )
        source_paths.append(path)
        source_entries.append(entries)

    writing_merges = []
    if source_paths:
        merged = _merged_writing_entries(target_entries, source_entries, target_user_id)
        writing_merges.append({
            "target": {
                "path": _relative_path(root, target_path),
                "exists_before": target_path.exists(),
                "sha256_before": _sha256_file(target_path) if target_path.exists() else None,
                "entry_count_before": len(target_entries),
            },
            "sources": [
                {
                    "path": _relative_path(root, path),
                    "sha256_before": _sha256_file(path),
                    "entry_count": len(entries),
                    "entry_ids": [entry["id"] for entry in entries],
                    "archive_path": _relative_path(
                        root, writing_dir / ".reconciled" / path.name
                    ),
                }
                for path, entries in zip(source_paths, source_entries)
            ],
            "entry_count_after": len(merged),
        })

    inventory = inventory_identity_stores(root)
    _assert_inventory_valid(inventory)
    manifest: dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "target_user_id": target_user_id,
        "approved_source_states": sources,
        "approved_writing_source_keys": writing_keys,
        "inventory": inventory,
        "session_changes": session_changes,
        "writing_merges": writing_merges,
        "summary": {
            "session_changes": len(session_changes),
            "writing_sources": len(source_paths),
            "writing_entries_after": writing_merges[0]["entry_count_after"] if writing_merges else 0,
        },
    }
    manifest["digest"] = manifest_digest(manifest)
    return manifest


def _verify_manifest(manifest: dict[str, Any], approved_digest: str) -> None:
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise MigrationRefused("unsupported manifest version")
    embedded = manifest.get("digest")
    calculated = manifest_digest(manifest)
    if not embedded or embedded != calculated or approved_digest != embedded:
        raise MigrationRefused("manifest digest does not match approval")
    if not _numeric_user_id(manifest.get("target_user_id")):
        raise MigrationRefused("manifest target identity is invalid")


def _verify_file_state(path: Path, expected_checksum: str | None, exists: bool = True) -> None:
    if path.exists() != exists:
        raise MigrationRefused(f"state changed since dry run: {path}")
    if exists and _sha256_file(path) != expected_checksum:
        raise MigrationRefused(f"state changed since dry run: {path}")


def _affected_originals(root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    paths: dict[str, dict[str, Any]] = {}
    for change in manifest["session_changes"]:
        paths[change["path"]] = {
            "path": change["path"],
            "existed": True,
            "sha256": change["sha256_before"],
        }
    for merge in manifest["writing_merges"]:
        target = merge["target"]
        paths[target["path"]] = {
            "path": target["path"],
            "existed": target["exists_before"],
            "sha256": target["sha256_before"],
        }
        for source in merge["sources"]:
            paths[source["path"]] = {
                "path": source["path"],
                "existed": True,
                "sha256": source["sha256_before"],
            }
    return [paths[key] for key in sorted(paths)]


def _preflight_apply(root: Path, manifest: dict[str, Any]) -> None:
    if inventory_identity_stores(root) != manifest.get("inventory"):
        raise MigrationRefused("identity-store state changed since dry run")

    for change in manifest["session_changes"]:
        path = _resolve_relative(root, change["path"])
        _verify_file_state(path, change["sha256_before"])
        record = _read_json(path, dict)
        if _identity_state(record, "user_id") != change["identity_before"]:
            raise MigrationRefused(f"state changed since dry run: {path}")

    target_user_id = manifest["target_user_id"]
    for merge in manifest["writing_merges"]:
        target_meta = merge["target"]
        target = _resolve_relative(root, target_meta["path"])
        _verify_file_state(
            target,
            target_meta["sha256_before"],
            exists=target_meta["exists_before"],
        )
        target_entries = _validated_entries(target)
        source_groups = []
        for source_meta in merge["sources"]:
            source = _resolve_relative(root, source_meta["path"])
            archive = _resolve_relative(root, source_meta["archive_path"])
            _verify_file_state(source, source_meta["sha256_before"])
            if archive.exists():
                raise MigrationRefused(f"archive destination already exists: {archive}")
            source_groups.append(_validated_entries(source))
        merged = _merged_writing_entries(target_entries, source_groups, target_user_id)
        if len(merged) != merge["entry_count_after"]:
            raise MigrationRefused("writing merge count changed since dry run")


def _create_backup(root: Path, backup_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    if backup_dir.exists():
        raise MigrationRefused(f"backup directory already exists: {backup_dir}")
    backup_dir.mkdir(parents=True, mode=0o700)
    originals = _affected_originals(root, manifest)
    for item in originals:
        if not item["existed"]:
            continue
        source = _resolve_relative(root, item["path"])
        destination = backup_dir / "originals" / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if _sha256_file(destination) != item["sha256"]:
            raise MigrationRefused(f"backup checksum mismatch: {item['path']}")

    generated_paths = [
        source["archive_path"]
        for merge in manifest["writing_merges"]
        for source in merge["sources"]
    ]
    backup_manifest = {
        "manifest_digest": manifest["digest"],
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "originals": originals,
        "generated_paths": sorted(generated_paths),
    }
    backup_manifest["backup_digest"] = _backup_manifest_digest(backup_manifest)
    _atomic_write_json(backup_dir / "backup_manifest.json", backup_manifest)
    return backup_manifest


def _restore_from_metadata(root: Path, backup_dir: Path, metadata: dict[str, Any]) -> None:
    for item in metadata["originals"]:
        destination = _resolve_relative(root, item["path"])
        if item["existed"]:
            source = backup_dir / "originals" / item["path"]
            if not source.exists() or _sha256_file(source) != item["sha256"]:
                raise MigrationRefused(f"backup missing or corrupt: {item['path']}")
            mode = source.stat().st_mode & 0o777
            _atomic_write_bytes(destination, source.read_bytes(), mode=mode)
        elif destination.exists():
            destination.unlink()

    for relative in metadata.get("generated_paths", []):
        generated = _resolve_relative(root, relative)
        if generated.exists():
            generated.unlink()

    for item in metadata["originals"]:
        destination = _resolve_relative(root, item["path"])
        if item["existed"]:
            if not destination.exists() or _sha256_file(destination) != item["sha256"]:
                raise MigrationRefused(f"restore verification failed: {item['path']}")
        elif destination.exists():
            raise MigrationRefused(f"restore left newly created file: {item['path']}")


def apply_manifest(
    data_root: str | Path,
    manifest: dict[str, Any],
    *,
    approved_digest: str,
    backup_dir: str | Path,
) -> dict[str, Any]:
    root = Path(data_root).resolve()
    backup = Path(backup_dir).resolve()
    try:
        backup.relative_to(root)
    except ValueError:
        pass
    else:
        raise MigrationRefused("backup directory must be outside the German data root")
    _verify_manifest(manifest, approved_digest)
    _preflight_apply(root, manifest)
    metadata = _create_backup(root, backup, manifest)
    target_user_id = manifest["target_user_id"]

    try:
        for change in manifest["session_changes"]:
            path = _resolve_relative(root, change["path"])
            record = _read_json(path, dict)
            record["user_id"] = target_user_id
            _atomic_write_json(path, record)

        for merge in manifest["writing_merges"]:
            target = _resolve_relative(root, merge["target"]["path"])
            target_entries = _validated_entries(target)
            source_groups = [
                _validated_entries(_resolve_relative(root, item["path"]))
                for item in merge["sources"]
            ]
            merged = _merged_writing_entries(target_entries, source_groups, target_user_id)
            _atomic_write_json(target, {"entries": merged})
            for source_meta in merge["sources"]:
                source = _resolve_relative(root, source_meta["path"])
                archive = _resolve_relative(root, source_meta["archive_path"])
                archive.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, archive)

        # Validate expected post-state before issuing a receipt.
        for change in manifest["session_changes"]:
            record = _read_json(_resolve_relative(root, change["path"]), dict)
            if record.get("user_id") != target_user_id:
                raise MigrationRefused(f"session validation failed: {change['path']}")
        for merge in manifest["writing_merges"]:
            target = _resolve_relative(root, merge["target"]["path"])
            entries = _validated_entries(target)
            if len(entries) != merge["entry_count_after"]:
                raise MigrationRefused("writing validation count mismatch")
            if any(entry.get("user_id") != target_user_id for entry in entries):
                raise MigrationRefused("writing validation identity mismatch")

        receipt = {
            "manifest_digest": manifest["digest"],
            "applied_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "session_changes": len(manifest["session_changes"]),
            "writing_sources": manifest["summary"]["writing_sources"],
        }
        _atomic_write_json(backup / "apply_receipt.json", receipt)
        return receipt
    except Exception as exc:
        try:
            _restore_from_metadata(root, backup, metadata)
        except Exception as restore_exc:
            raise MigrationRefused(
                f"apply failed and automatic restore also failed: {restore_exc}"
            ) from exc
        if isinstance(exc, MigrationRefused):
            raise
        raise MigrationRefused(f"apply failed; originals restored: {exc}") from exc


def restore_backup(
    data_root: str | Path,
    backup_dir: str | Path,
    *,
    approved_digest: str,
) -> None:
    root = Path(data_root).resolve()
    backup = Path(backup_dir).resolve()
    metadata = _read_json(backup / "backup_manifest.json", dict)
    if metadata.get("manifest_digest") != approved_digest:
        raise MigrationRefused("backup manifest digest does not match approval")
    if metadata.get("backup_digest") != _backup_manifest_digest(metadata):
        raise MigrationRefused("backup metadata digest is invalid")
    _restore_from_metadata(root, backup, metadata)


def _load_manifest(path: Path) -> dict[str, Any]:
    return _read_json(path, dict)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser("inventory", help="read-only identity inventory")
    inventory.add_argument("--data-root", type=Path, required=True)

    plan = subparsers.add_parser("plan", help="write a no-change manifest")
    plan.add_argument("--data-root", type=Path, required=True)
    plan.add_argument("--target-user-id", type=int, required=True)
    plan.add_argument("--session-source", action="append", default=[], required=True)
    plan.add_argument("--writing-source-key", action="append", default=[])
    plan.add_argument("--manifest-out", type=Path, required=True)

    apply = subparsers.add_parser("apply", help="apply an approved manifest")
    apply.add_argument("--data-root", type=Path, required=True)
    apply.add_argument("--manifest", type=Path, required=True)
    apply.add_argument("--approved-digest", required=True)
    apply.add_argument("--backup-dir", type=Path, required=True)

    restore = subparsers.add_parser("restore", help="restore an apply backup")
    restore.add_argument("--data-root", type=Path, required=True)
    restore.add_argument("--backup-dir", type=Path, required=True)
    restore.add_argument("--approved-digest", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "inventory":
            print(json.dumps(inventory_identity_stores(args.data_root), indent=2))
        elif args.command == "plan":
            manifest = build_manifest(
                args.data_root,
                target_user_id=args.target_user_id,
                session_sources=args.session_source,
                writing_source_keys=args.writing_source_key,
            )
            _atomic_write_json(args.manifest_out, manifest)
            print(json.dumps({"manifest": str(args.manifest_out), "digest": manifest["digest"], **manifest["summary"]}, indent=2))
        elif args.command == "apply":
            receipt = apply_manifest(
                args.data_root,
                _load_manifest(args.manifest),
                approved_digest=args.approved_digest,
                backup_dir=args.backup_dir,
            )
            print(json.dumps(receipt, indent=2))
        elif args.command == "restore":
            restore_backup(
                args.data_root,
                args.backup_dir,
                approved_digest=args.approved_digest,
            )
            print(json.dumps({"restored": True, "manifest_digest": args.approved_digest}, indent=2))
        return 0
    except MigrationRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
