#!/usr/bin/env python3
"""Reconcile every Vault source against the shelf and the production snapshot (17 Oct 2026). Read-only.

    reconcile.py [--shelf DIR] [--export ZIP ...] [--claude-projects DIR] [--codex-sessions DIR]
                 [--production-installed FILE] [--production-inventory FILE] [--sync-state DIR] [--sync-status FILE]
                 [--year 2026] [--out report.json]

For each source item (a conversation in a provider export, or a Claude Code / Codex session file) it decides one state:

    on_shelf     a record with that exact source key is on the shelf, and the ledger's latest outcome for the file is a
                 confirmed capture (captured, edition-added, unchanged), or the ledger has no row for it
    stale        on the shelf, but the latest outcome is anything else (unstable, discovered, disk_low, excluded, held,
                 ...): the newest content of the file is not confirmed on the shelf (its outcome[:reason]); a gap
    excluded     the capture ledger excluded it on purpose (its reason code: empty, no_turns, account data, ...)
    failed       the ledger's latest outcome for it is a failure (its reason code)
    held         held for review (unsupported kind, ...)
    not_seen     the ledger has never seen it: a real gap (a newer export or a capture run is needed)

and, with ``--production-installed`` (the EC2 snapshot's installed.json), whether production holds exactly the shelf's
record set per provider (key-set sha256 digests; no ids leave the host). Shelf records with no source item here are
listed as ``shelf_only`` counts (older exports, MC conversations, etc.), never as errors.

With ``--production-inventory`` (production's ``sync/acks/inventory.json``, written by ``vault-apply``) the report follows
every record from source -> shelf -> production, at the level of record VERSIONS (record id, edition, folder digest):

    production.versions   shelf and production version counts, ``identical_set`` (version-set digests equal), and how many
                          are missing, older or newer in production, or production-only; a version production lacks is
                          ``pending`` when the Mac's ``vault-sync`` has it in flight, otherwise a ``gap``
    production.gaps       those gaps by provider and month (12-character hashes of source keys, never ids or titles)
    sync                  ``last_sync_at`` (the ``vault-sync`` status's last run that ended with backlog 0), ``backlog_records``,
                          ``oldest_pending`` (from ``~/minimoi-staging/.vault-sync/pending.json``) and the inventory's age
    coverage              each provider export's newest conversation date: an ACCEPTED limit (exports are occasional
                          backfills), never a gap

Key-set digests are read from either receipt shape: ``source_keys`` (what installed.json and inventory.json carry) or
the older ``key_digests`` / ``counts`` / ``key_digest_all``.

The report holds counts, months and 12-character hashes of source keys only: never a title, a name or any text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
FAILED = {"failed", "refused"}
CONFIRMED = {"captured", "edition-added", "unchanged"}   # the only outcomes that confirm the shelf holds the newest content


def short(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def ledger_key(source: str, name: str) -> str:
    from core.memory_shelf.bundle import ledger_key_for            # the shelf's own function: never a drifting copy
    return ledger_key_for(source, name)


def key_digest(keys) -> str:
    return hashlib.sha256("\n".join(sorted(keys)).encode()).hexdigest()


def read_ledger(shelf: Path) -> dict[str, dict]:
    """The latest row per ledger key (rows are appended in time order)."""
    latest: dict[str, dict] = {}
    path = shelf / "_ledger" / "ledger.jsonl"
    if path.is_file():
        with path.open() as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("key"), str):
                    latest[row["key"]] = row
    return latest


def export_items(path: Path):
    """(source key, created day, skip reason, skip outcome) for each conversation in one provider export."""
    from core.memory_shelf import inbox
    for item in inbox.load_items(path, datetime.now(timezone.utc)):
        yield item.key, (item.created or "")[:10], item.skip, item.skip_outcome


def first_timestamp_month(path: Path, budget: int = 1 << 20) -> str:
    """The session's month from its first ``"timestamp"`` field (Claude Code names carry no date). Reads at most
    ``budget`` bytes; falls back to the file's modification time."""
    stamp = re.compile(rb'"timestamp"\s*:\s*"(20\d\d)-(\d\d)')
    try:
        with path.open("rb") as f:
            match = stamp.search(f.read(budget))
        if match:
            return f"{match.group(1).decode()}-{match.group(2).decode()}"
    except OSError:
        pass
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m")


def session_items(source: str, root: Path):
    """(source key, month, ledger key) for each Claude Code / Codex session file under root."""
    if source == "claude-code":
        files = sorted(root.glob("*/*.jsonl"))
    else:
        files = sorted(p for p in root.rglob("*") if p.name.endswith((".jsonl", ".jsonl.zst")))
    seen = set()
    for f in files:
        rel = f.relative_to(root).as_posix()
        rel = rel[:-4] if rel.endswith(".zst") else rel
        if rel in seen:                              # a session present both plain and compressed counts once
            continue
        seen.add(rel)
        match = UUID.search(f.name)
        if not match:
            continue
        dated = re.search(r"(20\d\d)-(\d\d)-\d\d", f.name)
        month = f"{dated.group(1)}-{dated.group(2)}" if dated else first_timestamp_month(f)
        yield f"{source}:{match.group(0)}", month, ledger_key(source, rel)


def receipt_digests(doc: dict) -> tuple[dict, dict, str | None]:
    """(per-provider digest, per-provider count, overall digest) from a production receipt of either shape."""
    keys = doc.get("source_keys")
    if isinstance(keys, dict) and isinstance(keys.get("by_provider"), dict):
        by = keys["by_provider"]
        return ({p: v.get("digest") for p, v in by.items() if isinstance(v, dict)},
                {p: v.get("count") for p, v in by.items() if isinstance(v, dict)}, keys.get("digest"))
    return doc.get("key_digests") or {}, doc.get("counts") or {}, doc.get("key_digest_all")


def compare_key_sets(doc: dict, shelf_keys: dict, index: dict) -> dict:
    digests, counts, overall = receipt_digests(doc)
    out = {p: {"shelf_records": len(keys), "production_records": counts.get(p), "identical_set": digests.get(p) == key_digest(keys)}
           for p, keys in sorted(shelf_keys.items())}
    for p in sorted(set(digests) - set(shelf_keys)):
        out[p] = {"shelf_records": 0, "production_records": counts.get(p), "identical_set": False}
    out["_all"] = {"identical_set": overall == key_digest(set(index))}
    return out


def _age_s(stamp, now: datetime):
    try:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((now - t).total_seconds()))


def compare_versions(shelf: Path, index: dict, inventory: dict, pending: dict) -> dict:
    """Record versions on the shelf against production's inventory. Ids never leave this function; gaps are counted by provider
    and month with 12-character source-key hashes."""
    from core.memory_sync import versions as V
    local, problems = V.record_versions(shelf)
    prod = V.parse_inventory(inventory)
    by_id = {e.get("id"): (key, e) for key, e in index.items() if isinstance(e, dict)}
    in_flight = {rid for rid, e in (pending.get("pending") or {}).items() if isinstance(e, dict)}
    counts, gaps = Counter(), []
    for rid, v in local.items():
        have = prod.get(rid)
        if have and have["digest"] == v.digest:
            counts["identical"] += 1
            continue
        if have and have["edition"] > v.edition:
            counts["newer_in_production"] += 1                       # flagged, never deleted or overwritten
            continue
        state = "missing_in_production" if have is None else "older_in_production"
        counts[state] += 1
        if rid in in_flight:
            counts["pending"] += 1
            continue
        counts["gaps"] += 1
        key, entry = by_id.get(rid, (v.source_key or "", {}))
        gaps.append({"provider": (key or "other").split(":", 1)[0], "month": str(entry.get("date") or "")[:7] or "unknown",
                     "key": short(key or rid), "state": state})
    counts["production_only"] = sum(1 for rid in prod if rid not in local)
    by_month = Counter((g["provider"], g["month"]) for g in gaps)
    return {"shelf_versions": len(local), "production_versions": len(prod),
            "identical_set": V.version_set_digest(V.entries_of(local)) == inventory.get("version_set_digest") and len(local) == len(prod),
            **{k: counts.get(k, 0) for k in ("identical", "missing_in_production", "older_in_production", "newer_in_production",
                                             "production_only", "pending", "gaps")},
            "unsyncable_on_shelf": int(sum(problems.values())),
            "gaps_by_provider_month": {f"{p}:{m}": n for (p, m), n in sorted(by_month.items())}, "gap_items": gaps}


def sync_summary(inventory: dict | None, pending: dict, status_doc: dict | None, now: datetime) -> dict:
    since = [str(t) for t in (pending.get("backlog_since") or {}).values()]
    oldest = min(since) if since else None
    results = (status_doc or {}).get("results") or {}
    return {"last_sync_at": (status_doc or {}).get("last_success_at"),
            "last_run_at": (status_doc or {}).get("finished_at"), "last_run_state": (status_doc or {}).get("state"),
            "backlog_records": len(set(pending.get("backlog_since") or {}) | set(pending.get("pending") or {})),
            "backlog_records_reported": results.get("backlog_records"),
            "oldest_pending": oldest, "oldest_pending_age_s": _age_s(oldest, now) if oldest else 0,
            "pending_in_flight": len(pending.get("pending") or {}),
            "inventory_generated_utc": (inventory or {}).get("generated_utc"),
            "inventory_age_s": _age_s((inventory or {}).get("generated_utc"), now) if inventory else None,
            "through_batch": (inventory or {}).get("through_batch")}


def reconcile(shelf: Path, exports, claude_projects: Path | None, codex_sessions: Path | None,
              installed: Path | None, year: str, *, inventory: Path | None = None, sync_state: Path | None = None,
              sync_status: Path | None = None, now: datetime | None = None) -> dict:
    index = json.loads((shelf / "_ledger" / "source_index.json").read_text())
    ledger = read_ledger(shelf)
    by_provider: dict[str, dict] = defaultdict(lambda: {"source_items": 0, "states": Counter(), "reasons": Counter(),
                                                         "months_on_shelf": Counter(), "gaps": []})
    expected: set[str] = set()
    newest_export: dict[str, str] = {}

    def place(provider, key, month, state, reason=None):
        row = by_provider[provider]
        row["source_items"] += 1
        row["states"][state] += 1
        if reason:
            row["reasons"][f"{state}:{reason}"] += 1
        if state == "on_shelf" and month.startswith(year):
            row["months_on_shelf"][month] += 1
        if state in ("not_seen", "failed", "stale"):
            row["gaps"].append({"key": short(key), "month": month, "state": state, "reason": reason})

    for path in exports:
        for key, day, skip, skip_outcome in export_items(Path(path)):
            provider, month = key.split(":", 1)[0], day[:7]
            expected.add(key)
            if day and day > newest_export.get(provider, ""):
                newest_export[provider] = day
            if key in index:
                place(provider, key, month, "on_shelf")
            elif skip and skip_outcome == "refused":
                place(provider, key, month, "failed", skip)              # e.g. unknown_sender: refused, not a deliberate exclusion
            elif skip:
                place(provider, key, month, "excluded", skip)
            else:
                place(provider, key, month, "not_seen")
    for source, root in (("claude-code", claude_projects), ("codex", codex_sessions)):
        if root is None:
            continue
        for key, month, lkey in session_items(source, Path(root)):
            expected.add(key)
            row = ledger.get(lkey)
            outcome, reason = (row or {}).get("outcome"), (row or {}).get("reason")
            if key in index and (row is None or outcome in CONFIRMED):
                place(source, key, month, "on_shelf")
            elif key in index and outcome in FAILED:
                place(source, key, month, "failed", reason or outcome)          # an older edition exists; the newest failed
            elif key in index:                                                  # an older edition exists; the newest unconfirmed
                place(source, key, month, "stale", f"{outcome}:{reason}" if reason else str(outcome))
            elif outcome == "excluded":
                place(source, key, month, "excluded", reason)
            elif outcome in FAILED:
                place(source, key, month, "failed", reason or outcome)
            elif outcome == "held":
                place(source, key, month, "held", reason)
            else:
                place(source, key, month, "not_seen")

    shelf_keys = defaultdict(set)
    for key in index:
        shelf_keys[key.split(":", 1)[0]].add(key)
    shelf_only = {p: len(keys - expected) for p, keys in shelf_keys.items() if keys - expected}

    production = None
    if installed is not None:
        production = compare_key_sets(json.loads(Path(installed).read_text()), shelf_keys, index)

    now = now or datetime.now(timezone.utc)
    inv_doc = json.loads(Path(inventory).read_text()) if inventory is not None else None
    pending = {}
    if sync_state is not None and (Path(sync_state) / "pending.json").is_file():
        pending = json.loads((Path(sync_state) / "pending.json").read_text())
    status_doc = None
    if sync_status is not None and Path(sync_status).is_file():
        from core.jobs import status as job_status
        status_doc = job_status.read_status(Path(sync_status).parent, Path(sync_status).name, "vault-sync")
    sync = None
    if inv_doc is not None:
        keysets = compare_key_sets(inv_doc, shelf_keys, index)
        if production is None:
            production = keysets
        else:
            production["inventory_key_sets"] = keysets                 # the snapshot receipt keeps its own comparison
        production["versions"] = compare_versions(shelf, index, inv_doc, pending)
        production["gaps"] = production["versions"].pop("gap_items")
        sync = sync_summary(inv_doc, pending, status_doc, now)
    elif pending or status_doc:
        sync = sync_summary(None, pending, status_doc, now)
    coverage = {p: {"export_through": d, "accepted_limit": True,
                    "note": "conversations after this date reach the Vault at the next provider export (an occasional backfill), not a gap"}
                for p, d in sorted(newest_export.items())}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "year": year,
        "shelf_records": len(index), "shelf_records_by_provider": {p: len(k) for p, k in sorted(shelf_keys.items())},
        "sources": {p: {**r, "states": dict(r["states"]), "reasons": dict(r["reasons"]),
                        "months_on_shelf": dict(sorted(r["months_on_shelf"].items()))} for p, r in sorted(by_provider.items())},
        "shelf_only": shelf_only, "production": production, "sync": sync, "coverage": coverage,
    }


def main(argv=None) -> int:
    from core.memory_shelf import config
    ap = argparse.ArgumentParser(prog="reconcile.py")
    ap.add_argument("--shelf")
    ap.add_argument("--export", action="append", default=[])
    ap.add_argument("--claude-projects", default=str(Path.home() / ".claude" / "projects"))
    ap.add_argument("--codex-sessions", default=str(Path.home() / ".codex" / "sessions"))
    ap.add_argument("--production-installed")
    ap.add_argument("--production-inventory", help="production's sync/acks/inventory.json (copied to the Mac; ids and hashes only)")
    ap.add_argument("--sync-state", default=str(Path.home() / "minimoi-staging" / ".vault-sync"), help="vault-sync's state folder")
    ap.add_argument("--sync-status", default=str(Path.home() / "minimoi-staging" / "data" / "jobs" / "vault-sync.json"))
    ap.add_argument("--year", default=str(datetime.now(timezone.utc).year))
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    shelf = Path(a.shelf).expanduser() if a.shelf else config.load().shelf_root
    report = reconcile(shelf, a.export, Path(a.claude_projects).expanduser() if a.claude_projects else None,
                       Path(a.codex_sessions).expanduser() if a.codex_sessions else None,
                       Path(a.production_installed) if a.production_installed else None, a.year,
                       inventory=Path(a.production_inventory).expanduser() if a.production_inventory else None,
                       sync_state=Path(a.sync_state).expanduser() if a.sync_state else None,
                       sync_status=Path(a.sync_status).expanduser() if a.sync_status else None)
    text = json.dumps(report, indent=2, sort_keys=True)
    if a.out:
        fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)     # counts only, but about private history
        with os.fdopen(fd, "w") as f:
            f.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
