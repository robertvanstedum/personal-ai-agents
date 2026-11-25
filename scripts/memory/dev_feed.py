#!/usr/bin/env python3
"""Bounded Dev chat ingestion and citation verification. No model/network calls.

Requires explicit operator paths. CoS history and current MC exports are read
only. Each run replays snapshots through the shelf's idempotent edition writer.
This is chat coverage, not a claim to ingest Records or task/work journals.

``--mc-index-path`` (#594): after the full index is published, the same admitted records are indexed again into Master Craftsman's
index with the career filter (``core/memory_index/career_filter.py``; generic rules from the repository plus
``--career-rules-private`` when given), checked (profile and digest) and published atomically. Its outcome is reported under
``mc_index`` and never fails the feed: CoS's Ask must not stop because MC's index could not be built (MC's then keeps the last
filtered one, or has none).
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
from core.memory_index import career_filter, indexer, mc_index, store, resolver, policy

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


def _embedder(url):
    if not url:
        return None
    from core.memory_index.embedder import OllamaEmbedder
    return OllamaEmbedder(url)


def build_mc_index(shelf_root, mc_index_path, record_ids, career_rules_private=None, min_free=MIN_FREE, embedder=None,
                   donor_path=None) -> dict:
    """Index ``record_ids`` into a fresh career-filtered candidate, verify it, publish it over ``mc_index_path``. With ``embedder``
    (#660) a meaning index whose kept chunks borrow the just-published full index's vectors (``donor_path``); the career filter runs
    before any vector is taken, so no career turn is embedded or kept."""
    try:
        career = career_filter.load(None, career_rules_private)
    except career_filter.FilterError as exc:
        return {"code": exc.code}
    target = Path(mc_index_path)
    candidate = target.with_name(".dev-feed-mc-" + secrets.token_hex(8) + ".sqlite")
    try:
        out = indexer.update(shelf_root, embedder, index_path=candidate, keyword_only=embedder is None, min_free_bytes=min_free,
                             only=list(record_ids), career=career, donor_path=donor_path if embedder is not None else None)
        if out.get("code") != "ok":
            return {"code": out.get("code")}
        reason = mc_index.verify(candidate, career, "keyword" if embedder is None else "meaning")
        if reason:
            return {"code": reason}
        os.replace(candidate, target)
        return {"code": "ok", "indexed": out.get("indexed", 0), "held": out.get("held", {}),
                "career_turns_excluded": out.get("career_turns_excluded", 0), "career_filter": career.digest[:12],
                "kind": "keyword" if embedder is None else "meaning", "embedded_chunks": out.get("embedded_chunks", 0)}
    except Exception:
        return {"code": "mc_index_failed"}
    finally:
        candidate.unlink(missing_ok=True)
        candidate.with_name(candidate.name + ".lock").unlink(missing_ok=True)


def run(*, cos_root, mc_root, shelf_root, index_path, jobs_root, min_free=MIN_FREE, mc_index_path=None,
        career_rules_private=None, embedder_url=None, embedder=None):
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
        mc_built = {"code": "not_configured"} if not mc_index_path else {"code": "not_run"}
        emb = embedder if embedder is not None else _embedder(embedder_url)     # #660: meaning indexes when configured
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
            # With the embedder (#660) the previous published index lends the vectors of unchanged text: each run builds a fresh
            # candidate, so without a donor it would embed the whole shelf every five minutes.
            donor = Path(index_path) if emb is not None and Path(index_path).is_file() else None
            indexed = indexer.update(shelf_root, emb, index_path=candidate, keyword_only=emb is None, min_free_bytes=min_free,
                                     only=[r["record_id"] for r in receipts], donor_path=donor)
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
            if mc_index_path and not errors:
                mc_built = build_mc_index(shelf_root, mc_index_path, [r["record_id"] for r in receipts],
                                          career_rules_private, min_free, embedder=emb, donor_path=Path(index_path))
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
                  "index": indexed, "verification": checked, "receipts": receipts, "mc_index": mc_built,
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
    parser.add_argument("--mc-index-path", type=Path, help="also publish MC's career-filtered index here (#594)")
    parser.add_argument("--career-rules-private", type=Path, help="the host's private career rules (0600, never in git)")
    parser.add_argument("--embedder-url", help="#660: the pinned embedder (http://vault-embedder:11434): meaning indexes")
    args = parser.parse_args()
    os.umask(0o077)
    result = run(**vars(args))
    print(json.dumps({"state": result["state"], "counts": result.get("counts", {}), "errors": result.get("errors", {}),
                      "mc_index": result.get("mc_index", {}).get("code")}))
    return 0 if result["state"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
