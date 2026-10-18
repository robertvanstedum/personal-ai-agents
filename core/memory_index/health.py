"""Index health (v0.5 C3/C7, F2; amendment R4): freshness, orphans, model pin, internal consistency, F2 sweep.

All read-only. Every output is counts and fixed codes: no record text, no titles, no paths.

* **Freshness**: a record the shelf lists but the index has not seen (``unindexed``) or whose file changed since it
  was indexed (``changed``) is *pending*. Held records are counted by reason. ``awaiting_reprocess`` is expected
  while the capture revision re-mediates old editions and does not by itself make the light yellow; the other
  hold reasons (``parse_mismatch``, ``unreadable``, ``invalid_record``, ``unknown_provider``) do.
* **Orphans** (a failed check): a chunk or record row whose record the shelf no longer lists, whose file is gone,
  or that has no record row; a chunk without its vector; a vector or keyword row without its chunk.
* **Model pin**: the live embedder against the pin in code and against the identity the index was built with.
* **Light** (jobs-contract vocabulary): ``failed`` on orphans, inconsistency, a pin mismatch or an incompatible
  index; ``warn`` when anything is pending or a problem hold exists, or the embedder cannot be checked; ``unknown``
  when there is no readable index; else ``ok``.
* **F2 sweep** (``fidelity_sweep``): the shelf's own fidelity check (``fidelity.check_record``) over **every**
  record, run after a rebuild; counts by outcome and reason.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from core.memory_index import indexer, store
from core.memory_index.embedder import Embedder, EmbedderUnavailable, PinMismatch

EXPECTED_HOLDS = frozenset({"awaiting_reprocess", "no_dialogue", "unsupported_kind"})


def _pin(con, embedder: Embedder | None) -> str:
    if embedder is None:
        return "not_checked"
    try:
        ident = embedder.check_pin()
    except PinMismatch:
        return "mismatch"
    except EmbedderUnavailable:
        return "unavailable"
    built = store.get_meta(con).get("embedder_digest")
    return "ok" if built in (None, ident.digest) else "mismatch"


def orphans(con, shelf_root: Path, listed: dict[str, str]) -> dict[str, int]:
    out: Counter = Counter()
    rows = dict(con.execute("SELECT record_id, rel FROM records").fetchall())
    for rid, rel in rows.items():
        if rid not in listed:
            out["record_not_on_shelf"] += 1
        elif not (shelf_root / rel).is_file():
            out["record_file_missing"] += 1
    gone = [rid for rid in rows if rid not in listed or not (shelf_root / rows[rid]).is_file()]
    if gone:
        marks = ",".join("?" * len(gone))
        out["chunks_of_missing_records"] = con.execute(
            f"SELECT COUNT(*) FROM chunks WHERE record_id IN ({marks})", gone).fetchone()[0]
    out["chunks_without_record_row"] = con.execute(
        "SELECT COUNT(*) FROM chunks WHERE record_id NOT IN (SELECT record_id FROM records)").fetchone()[0]
    if not store.is_keyword_only(con):                # a keyword-only index has no vector table to compare
        out["chunks_without_vector"] = con.execute(
            "SELECT COUNT(*) FROM chunks WHERE id NOT IN (SELECT id FROM vec)").fetchone()[0]
        out["vectors_without_chunk"] = con.execute(
            "SELECT COUNT(*) FROM vec WHERE id NOT IN (SELECT id FROM chunks)").fetchone()[0]
    out["keyword_rows_without_chunk"] = con.execute(
        "SELECT COUNT(*) FROM fts_docsize WHERE id NOT IN (SELECT id FROM chunks)").fetchone()[0]
    return {k: v for k, v in sorted(out.items()) if v}


def status(shelf_root: str | Path, *, index_path: str | Path | None = None, embedder: Embedder | None = None) -> dict:
    shelf_root = Path(shelf_root)
    index_path = Path(index_path) if index_path else indexer.default_index_path(shelf_root)
    try:
        entries = indexer.shelf_entries(shelf_root)
    except Exception as exc:                          # noqa: BLE001 - fixed code only
        return {"light": "unknown", "reason": f"shelf_unreadable:{type(exc).__name__}"}
    listed = {rid: rel for rid, rel, _ in entries}
    try:
        con = store.connect(index_path, readonly=True)
    except store.IndexMissing:
        return {"light": "unknown", "reason": "no_index", "shelf_records": len(entries)}
    try:
        if indexer.check_compatible(con, None):
            return {"light": "failed", "reason": indexer.INCOMPATIBLE, "shelf_records": len(entries)}
        meta = store.get_meta(con)
        rows = {r[0]: r[1:] for r in con.execute(
            "SELECT record_id, file_size, file_mtime_ns, state, reason, chunks FROM records")}
        fresh: Counter = Counter()
        held: Counter = Counter()
        for rid, _rel, path in entries:
            row = rows.get(rid)
            if row is None:
                fresh["unindexed"] += 1
                continue
            try:
                st = path.stat()
            except OSError:
                fresh["file_missing"] += 1
                continue
            if (st.st_size, st.st_mtime_ns) != (row[0], row[1]):
                fresh["changed"] += 1
            elif row[2] == "held":
                held[row[3]] += 1
            else:
                fresh["current"] += 1
        orphan = orphans(con, shelf_root, listed)
        pin = _pin(con, embedder)
        chunks = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        con.close()
    pending = fresh["unindexed"] + fresh["changed"] + fresh["file_missing"]
    problems = {k: v for k, v in held.items() if k not in EXPECTED_HOLDS}
    if orphan or pin == "mismatch":
        light = "failed"
    elif pending or problems or pin == "unavailable":
        light = "warn"
    else:
        light = "ok"
    return {"light": light, "shelf_records": len(entries), "indexed_current": fresh["current"],
            "pending": {k: fresh[k] for k in ("unindexed", "changed", "file_missing") if fresh[k]},
            "held": dict(sorted(held.items())), "orphans": orphan, "model_pin": pin, "chunks": chunks,
            "embedder": f"{meta.get('embedder')}@{str(meta.get('embedder_digest'))[:12]}",
            "built_at": meta.get("built_at"), "updated_at": meta.get("updated_at"),
            "index_bytes": index_path.stat().st_size}


def fidelity_sweep(shelf_root: str | Path, cfg=None) -> dict:
    """F2 over every record (the rebuild sweep). Counts by outcome; failures listed by record id and finding kind."""
    from core.memory_shelf import config, fidelity
    cfg = cfg or config.load()
    out: Counter = Counter()
    skipped: Counter = Counter()
    failed: list[dict] = []
    for rid, _rel, path in indexer.shelf_entries(Path(shelf_root)):
        try:
            result = fidelity.check_record(cfg, path)
        except Exception as exc:                      # noqa: BLE001 - fixed code only
            out["error"] += 1
            skipped[f"error:{type(exc).__name__}"] += 1
            continue
        out[result["status"]] += 1
        if result["status"] == "skipped":
            skipped[result["reason"]] += 1
        elif result["status"] == "failed":
            failed.append({"record": rid, "findings": sorted({f["kind"] for f in result.get("findings", [])})})
    return {"checked": out["ok"] + out["failed"], "ok": out["ok"], "failed": out["failed"], "errors": out["error"],
            "skipped": dict(sorted(skipped.items())), "failures": failed}


__all__ = ["status", "orphans", "fidelity_sweep"]
