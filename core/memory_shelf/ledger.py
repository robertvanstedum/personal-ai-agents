"""The capture ledger (F1, amendment R4): captured vs expected exclusion vs missing.

One append-only row per considered source item and outcome, in
``_ledger/ledger.jsonl``. Rows carry a source label, an opaque item key, an
outcome code, a reason code and counts: never a file name or any text. The
latest row for each (source, key) is that item's state. The report separates:

* **captured**: the record exists on the shelf (checked, not assumed);
* **expected exclusion**: ``never_copy``, ``private``, ``empty``, and scrub or
  omission counts read from the record manifest, grouped by reason code;
* **refused**: an inbox file that was refused visibly, with its reason code;
* **missing**: seen but no outcome (a crash), failed, unstable, ``disk_low``,
  or captured on paper with no record on the shelf.

Canaries are counted apart and never in the ordinary totals.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from core.memory_shelf import codes, fsio


def _file(shelf) -> Path:
    return Path(shelf.ledger_dir) / "ledger.jsonl"


def record(shelf, source: str, key: str, outcome: str, *, reason: str | None = None, record_id: str | None = None,
           canary: bool = False, redacted_turns: int = 0, omitted: dict | None = None, turns: int = 0,
           now: datetime | None = None) -> dict:
    row = {"at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": source, "key": key,
           "outcome": outcome}
    if reason:
        row["reason"] = reason
    if record_id:
        row["record"] = record_id
    if canary:
        row["canary"] = True
    if turns:
        row["turns"] = turns
    if redacted_turns:
        row["redacted_turns"] = redacted_turns
    if omitted:
        row["omitted"] = dict(omitted)
    fsio.append_jsonl(_file(shelf), row)
    return row


def latest(shelf) -> dict[tuple[str, str], dict]:
    state: dict[tuple[str, str], dict] = {}
    for row in fsio.read_jsonl(_file(shelf)):
        if "source" in row and "key" in row:
            state[(row["source"], row["key"])] = row
    return state


def report(shelf, *, canary: bool = False) -> dict:
    """Counts only. ``ok`` is True when nothing is missing."""
    per: dict[str, dict] = defaultdict(lambda: {"expected": 0, "captured": 0, "excluded": Counter(),
                                                "refused": Counter(), "missing": Counter()})
    scrub, omitted = 0, Counter()
    for (source, _key), row in latest(shelf).items():
        if bool(row.get("canary")) != canary:
            continue
        out, reason = row["outcome"], row.get("reason") or "unspecified"
        bucket = per[source]
        bucket["expected"] += 1
        if out in codes.OK_OUTCOMES:
            if row.get("record") and not shelf.has_record(row["record"]):
                bucket["missing"]["record_absent"] += 1
                continue
            bucket["captured"] += 1
            scrub += row.get("redacted_turns", 0)
            omitted.update(row.get("omitted") or {})
        elif out == codes.EXCLUDED:
            bucket["excluded"][reason] += 1
        elif out == codes.REFUSED:
            bucket["refused"][reason] += 1
        else:                                   # discovered, failed, unstable, disk_low
            bucket["missing"][out] += 1
    sources = {s: {"expected": b["expected"], "captured": b["captured"], "excluded": dict(b["excluded"]),
                   "refused": dict(b["refused"]), "missing": dict(b["missing"])} for s, b in sorted(per.items())}
    missing = sum(sum(b["missing"].values()) for b in sources.values())
    return {"canary": canary, "sources": sources, "missing": missing, "ok": missing == 0,
            "expected_exclusions": {"scrubbed_turns": scrub, "omitted_by_kind": dict(sorted(omitted.items()))}}
