"""Fidelity F2, sampled (amendment R4): did the shelf keep exactly what the source said?

For a record, the retained source is re-read **fresh** and compared, as an ordered
sequence ``(ordinal, speaker, sha256(text))``, against the turns stored on the shelf
(the record body) and against its edition file. Both sides are scrubbed the same way
first, so an intentional scrub is never reported as damage. Containment checks are
not enough: this finds **dropped**, **duplicated**, **reordered**, **misattributed**
and **unexpected** (altered or inserted) turns, plus **edition_mismatch** (record
body and edition file disagree).

Diffs name positions and 12-character hash prefixes only: never turn text, so a diff
can leak neither excluded nor sanitized content. A source that no longer matches the
record's ``source_hash`` (the session kept growing, a newer edition is pending) or is
gone is **skipped with a reason**, not failed: it cannot be compared fairly.

The re-parse reuses the same format parsers as the writer, on freshly read bytes; it
checks the render, store and edition layers, not the parsers themselves (the parsers
have their own fixtures).
"""
from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

from core.memory_shelf import editions, inbox, record, render, sessions, watchers
from core.memory_shelf.config import Config

SOURCE_CHANGED, SOURCE_MISSING, NOT_RETAINED, UNREADABLE = "source_changed", "source_missing", "not_retained", "unreadable"
NORMALIZER_CHANGED = "normalizer_changed"          # the record was read by an older parser; re-mediation will bring it up to date


def diff(expected: list[tuple], actual: list[tuple], label: str = "") -> list[dict]:
    """Findings between two ordered ``(ordinal, speaker, sha)`` lists. Empty when identical."""
    if expected == actual:
        return []
    prefix = f"{label}:" if label else ""
    out: list[dict] = []

    def add(kind, position, e=None, a=None):
        out.append({"kind": prefix + kind, "position": position, "expected": e and e[:12], "actual": a and a[:12]})
    if [e[1:] for e in expected] == [a[1:] for a in actual]:       # same turns in the same order, numbered differently
        add("ordinals", 0)
        return out
    if len(expected) == len(actual):
        pairs = list(zip(expected, actual))
        mis = [(e, a) for e, a in pairs if e[2] == a[2] and e[1] != a[1]]
        rest = [(e, a) for e, a in pairs if e != a and not (e[2] == a[2] and e[1] != a[1])]
        if mis and not rest:
            for e, a in mis:
                add("misattributed", e[0], e[2], a[2])
            return out
        if rest and not mis and Counter((e[1], e[2]) for e, _ in rest) == Counter((a[1], a[2]) for _, a in rest):
            for e, a in rest:
                add("reordered", e[0], e[2], a[2])
            return out
    exp_c, act_c = Counter((e[1], e[2]) for e in expected), Counter((a[1], a[2]) for a in actual)
    seen: Counter = Counter()
    for e in expected:
        seen[(e[1], e[2])] += 1
        if seen[(e[1], e[2])] > act_c[(e[1], e[2])]:
            add("dropped", e[0], e[2])
    seen, exp_hashes = Counter(), {e[2] for e in expected}
    for a in actual:
        seen[(a[1], a[2])] += 1
        if seen[(a[1], a[2])] > exp_c[(a[1], a[2])]:
            add("duplicated" if a[2] in exp_hashes else "unexpected", a[0], None, a[2])
    return out


def shelf_turns(main: Path) -> list[tuple[int, str, str]]:
    _, body = record.load(main.read_text("utf-8"))
    return render.ordered_turns(render.parse_body(body))


def edition_turns(folder: Path, number: int) -> list[tuple[int, str, str]] | None:
    for ed in editions.list_editions(folder):
        if ed.number == number:
            rows = []
            with open(ed.path, "rb") as handle:
                handle.readline()                                  # header
                for line in handle:
                    row = json.loads(line)
                    if render.sha256_text(row["text"]) != row["sha256"]:
                        return rows + [(-1, "?", "0" * 64)]        # the edition's own hash line disagrees with its text
                    rows.append((row["ordinal"], row["speaker"], row["sha256"]))
            return rows
    return None


def _source_turns(cfg: Config, meta: dict):
    """(expected ordered turns, source sha256) from the retained source, or (None, reason)."""
    ret = meta.get("retained") or {}
    try:
        if ret.get("kind") == "source-file":
            source = cfg.sources.get(ret.get("source"))
            path = source and Path(source.root) / ret["rel"]
            if not path or not path.is_file():
                return None, SOURCE_MISSING
            sha, size = render.hash_file(path)
            if (meta.get("normalized") or {}).get("normalizer", sessions.DEFAULT_NORMALIZER) != sessions.NORMALIZER_VERSION[source.kind]:
                return None, NORMALIZER_CHANGED
            with open(path, "rb") as handle:
                parsed = watchers.PARSERS[source.kind](handle)
        elif ret.get("kind") == "inbox-file":
            path = Path(cfg.inbox_root) / ret["rel"]
            if not path.is_file():
                return None, SOURCE_MISSING
            from datetime import datetime, timezone
            fallback = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            member = ret.get("member")
            items = inbox.load_items(path, fallback, only=member)
            item = next((i for i in items if i.member == member), None) if member else next(iter(items), None)
            if item is None:
                return None, SOURCE_MISSING
            parsed, sha = item.parsed, item.source_sha256
        else:
            return None, NOT_RETAINED
    except (OSError, inbox.Refusal, ValueError, KeyError):
        return None, UNREADABLE
    clean, _ = render.scrub_parsed(parsed)
    return (render.ordered_turns(clean.turns), sha), None


def check_record(cfg: Config, main: Path) -> dict:
    """One record's F2 result: ``{"record", "status": ok|failed|skipped, "findings"|"reason"}``."""
    meta, _ = record.load(main.read_text("utf-8"))
    base = {"record": meta["id"]}
    got, reason = _source_turns(cfg, meta)
    if got is None:
        return {**base, "status": "skipped", "reason": reason}
    expected, sha = got
    if sha != meta.get("source_hash"):
        return {**base, "status": "skipped", "reason": SOURCE_CHANGED}
    findings = diff(expected, shelf_turns(main))
    on_edition = edition_turns(main.parent, int(meta.get("edition", 1)))
    if on_edition is None or on_edition != shelf_turns(main):
        findings += diff(shelf_turns(main), on_edition or [], "edition_mismatch")
    return {**base, "status": "failed" if findings else "ok", "findings": findings}


def sample(shelf, cfg: Config, n: int = 5, *, canary: bool = False, seed: int | None = None) -> dict:
    """Check ``n`` random ordinary records (or the canaries, when asked by name). Read-only."""
    entries = shelf.list_records(canary=canary)
    chosen = random.Random(seed).sample(entries, min(n, len(entries)))
    results = [check_record(cfg, shelf.main_path(e)) for e in chosen]
    skipped = Counter(r["reason"] for r in results if r["status"] == "skipped")
    return {"canary": canary, "checked": sum(r["status"] != "skipped" for r in results),
            "ok": sum(r["status"] == "ok" for r in results), "failed": sum(r["status"] == "failed" for r in results),
            "skipped": dict(skipped), "results": [r for r in results if r["status"] != "ok"]}
