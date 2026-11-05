#!/usr/bin/env python3
"""Bounded Dev chat ingestion and citation verification. No model/network calls.

Requires explicit operator paths. CoS history and current MC exports are read
only. Each run replays snapshots through the shelf's idempotent edition writer.
This is chat coverage, not a claim to ingest Records or task/work journals.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.jobs import status
from core.memory_shelf import cos, mc, codes
from core.memory_shelf.bundle import from_parsed
from core.memory_shelf.shelf import Shelf, read_front_matter
from core.memory_index import indexer, store, resolver, policy

MAX_FILES = 2000
MAX_SECONDS = 240
MIN_FREE = 1024 ** 3
JOB = SimpleNamespace(id="dev-memory-feed", status_file="dev-memory-feed.json", host="dev")


def utc():
    return datetime.now(timezone.utc)


def mc_inventory(root):
    receipt = json.loads(cos.snapshot(root / "feed-export.json", 1024 * 1024))
    age = (utc() - datetime.fromisoformat(receipt["completed_at"].replace("Z", "+00:00"))).total_seconds()
    if receipt.get("schema") != "minimoi.dev-mc-export/1" or receipt.get("status") != "ok" or not 0 <= age <= 600:
        raise cos.Refused("export_stale")
    files = receipt["files"]
    if not isinstance(files, dict) or len(files) > MAX_FILES or any(not mc.wanted(n) or not n.startswith("dev--") for n in files):
        raise cos.Refused("export_inventory")
    return files


def verify(shelf_root, index_path, limit=20):
    """Resolve a rotating sample of actual index rows against source editions."""
    con = store.connect(index_path, readonly=True)
    try:
        total = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        if not total:
            return {"checked": 0, "failed": 0, "chunks": 0}
        offset = int(time.time() // 300) * limit % total
        cols = "chunk_id,record_id,edition_hash,turn,speaker,rel,start_line,end_line,text_hash,scope"
        rows = con.execute(f"SELECT {cols} FROM chunks ORDER BY id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        failures = 0
        for row in rows:
            hit, gap = resolver.resolve(Path(shelf_root), dict(zip(cols.split(','), row)),
                                        policy.Caller(tier="all", scopes=("robert",)), "keyword")
            failures += int(hit is None or gap is not None)
        return {"checked": len(rows), "failed": failures, "chunks": total}
    finally:
        con.close()


def run(*, cos_root, mc_root, shelf_root, index_path, jobs_root, min_free=MIN_FREE):
    jobs_root = Path(jobs_root)
    jobs_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Never overwrite another live run's status or take its lock.
    with open(jobs_root / ".dev-memory-feed.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"state": "busy"}
        running = status.start_run(jobs_root, JOB, secrets.token_hex(12), utc())
        started = time.monotonic()
        counts, errors, receipts = Counter(), Counter(), []
        shelf = Shelf(shelf_root, min_free_bytes=min_free, min_free_fraction=None)
        candidate = Path(index_path).with_name(".dev-feed-" + secrets.token_hex(8) + ".sqlite")
        try:
            checkpoint_path = jobs_root / "dev-memory-cursors.json"
            checkpoints = json.loads(cos.snapshot(checkpoint_path, 1024 * 1024)) if checkpoint_path.exists() else {}
            candidates = []
            for family, root, pattern in (("cos", Path(cos_root) / "conversations", "*.json"),
                                          ("mc", Path(mc_root), "dev--*.json")):
                if not root.is_dir() or root.is_symlink():
                    errors[family + "_missing"] += 1
                    continue
                if family == "mc":
                    try:
                        inventory = mc_inventory(root)
                        found = [root / name for name in sorted(inventory)]
                    except (OSError, ValueError, KeyError, TypeError):
                        errors["mc_export_invalid"] += 1
                        continue
                else:
                    found = sorted(root.glob(pattern))
                if len(found) > MAX_FILES:
                    errors["file_limit"] += 1
                    continue
                if not found:
                    errors[family + "_empty"] += 1
                candidates.extend((family, path) for path in found)
            for family, path in candidates:
                if time.monotonic() - started > MAX_SECONDS:
                    errors["run_deadline"] += 1
                    break
                counts[family + "_seen"] += 1
                try:
                    if family == "cos":
                        parsed, raw = cos.read(Path(cos_root), path.stem, checkpoint=checkpoints.get(path.stem))
                    else:
                        raw = cos.snapshot(path)
                        if hashlib.sha256(raw).hexdigest() != inventory[path.name]:
                            raise cos.Refused("export_changed")
                        parsed = mc.parse([raw])
                        if parsed.source_id != path.stem.replace("--", ":", 1):
                            raise cos.Refused("bad_identity")
                    if not parsed.turns:
                        counts[family + "_empty"] += 1
                        continue
                    digest = hashlib.sha256(raw).hexdigest()
                    key = f"{parsed.provider}:{parsed.source_id}"
                    bundle = from_parsed(parsed, digest, len(raw), key=key, title="Dev conversation",
                                         origin="watch:dev", created=utc().isoformat().replace("+00:00", "Z"))
                    result = shelf.ingest(bundle)
                    if result.outcome not in codes.OK_OUTCOMES:
                        errors[family + "_ingest"] += 1
                    else:
                        counts[family + "_consumed"] += 1
                        entry = shelf.index()[key]
                        meta = read_front_matter(shelf.main_path(entry))
                        dialogue_expected = bool(policy.retrievable_turns(parsed.turns))
                        if not dialogue_expected:
                            counts[family + "_evidence_only"] += 1
                        receipts.append({"source": key, "source_sha256": digest, "record_id": result.record_id,
                                         "edition_hash": meta["edition_hash"], "dialogue_expected": dialogue_expected,
                                         "outcome": result.outcome})
                        if family == "cos":
                            checkpoints[path.stem] = parsed.manifest["cos_journal_checkpoint"]
                            status.write_json_atomic(jobs_root, checkpoint_path.name, checkpoints)
                except cos.Refused as exc:
                    if str(exc) == "private":
                        counts["cos_private"] += 1
                    else:
                        errors[family + "_refused"] += 1
                except (OSError, ValueError, TypeError, KeyError):
                    errors[family + "_unreadable"] += 1
            # Build a new derived index containing only this run's admitted source
            # set. Removed/private/refused records remain evidence on the shelf,
            # but cannot remain in retrieval. Atomic publication avoids a window
            # in which an update temporarily reintroduces historical exclusions.
            indexed = indexer.update(shelf_root, None, index_path=candidate, keyword_only=True, min_free_bytes=min_free,
                                     only=[r["record_id"] for r in receipts])
            if indexed.get("code") != "ok" or indexed.get("held"):
                errors["index_not_ready"] += 1
            con = store.connect(candidate, readonly=True)
            try:
                for receipt in receipts:
                    row = con.execute("SELECT edition_hash,state,chunks FROM records WHERE record_id=?",
                                      (receipt["record_id"],)).fetchone()
                    if not row or row[0] != receipt["edition_hash"] or (receipt["dialogue_expected"] and row[2] == 0):
                        errors["receipt_not_indexed"] += 1
            finally:
                con.close()
            checked = verify(shelf_root, candidate)
            if not checked["chunks"] or checked["failed"]:
                errors["citation_not_ready"] += 1
            os.replace(candidate, index_path)
        except Exception:
            # No raw exceptions or content in operational status.
            errors["run_failed"] += 1
            indexed, checked = {}, {}
        finally:
            candidate.unlink(missing_ok=True)
            # Only our own per-run lock artifact, never source/shelf evidence.
            candidate.with_name(candidate.name + ".lock").unlink(missing_ok=True)
        state = "failed" if errors else "ok"
        report = {"schema": "minimoi.dev-memory-feed/1", "at": utc().isoformat(), "state": state,
                  "coverage": "cos_turns_and_mc_exports_only", "counts": dict(counts), "errors": dict(errors),
                  "index": indexed, "verification": checked, "receipts": receipts,
                  "pruning": "disabled_no_source_or_edition_deletion"}
        status.write_json_atomic(jobs_root, "dev-memory-feed-report.json", report)
        status.finish_run(jobs_root, JOB, running, state=state, now=utc(), exit_code=int(bool(errors)),
                          summary="Dev chat capture and citation check", results={
                              "capture": "failed" if errors else "ok", "coverage": "chat_only",
                              "pruning": "disabled", "errors": str(sum(errors.values()))},
                          next_due_by=utc() + timedelta(minutes=10), success=not errors)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cos-root", "mc-root", "shelf-root", "index-path", "jobs-root"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    result = run(**vars(args))
    print(json.dumps({"state": result["state"], "counts": result.get("counts", {}), "errors": result.get("errors", {})}))
    return 0 if result["state"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
