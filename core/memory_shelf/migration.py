"""Counts-only migration preview and inbox reprocessing for a normalizer / policy revision (capture-fixes slice).

When a parser's rules change (a human turn stored as system, approval-review traffic in the dialogue, handoffs left out,
attachment text in the dialogue), existing records must not be rewritten: **editions are immutable**. The next watcher pass
re-reads an unchanged source with the new parser and, if its turns differ, adds a new edition; the old editions stay. The
current edition is what retrieval reads, so legacy misattribution leaves ordinary retrieval as soon as the corrected edition
exists, while history stays under the existing retention policy (a stricter purge is a separate owner decision).

``preview`` answers "what would that do?" **before** anything runs: read-only, and it prints **counts and fixed codes only**
(no text, title, file name or path). For every record whose source is still readable it parses the source with the current
rules and compares it with the stored current edition, line by line, counting role transitions (``system->human``,
``human->coordination`` ...), turns added or removed, and text changed in place; it also counts the records that would get a
new edition. Sources that changed since capture, are gone, or cannot be read are counted apart and never compared.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from core.memory_shelf import approvals, codes, fidelity, record, render, sessions
from core.memory_shelf import bundle as bundles
from core.memory_shelf.config import Config


def current_version(provider: str) -> int:
    return sessions.NORMALIZER_VERSION.get(provider, sessions.DEFAULT_NORMALIZER)


def _by_line(turns) -> dict:
    keyed: dict = {}
    seen: Counter = Counter()
    for t in turns:
        key = (t.line, seen[t.line])
        seen[t.line] += 1
        keyed[key] = t
    return keyed


def align(old_turns, new_turns) -> Counter:
    """Transitions between the stored turns and the turns the current rules would store, keyed by source line."""
    old, new = _by_line(old_turns), _by_line(new_turns)
    out: Counter = Counter()
    for key in old.keys() | new.keys():
        a, b = old.get(key), new.get(key)
        if a and b:
            if render.sha256_text(a.text) != render.sha256_text(b.text):
                out["text_changed"] += 1
            elif a.speaker != b.speaker or a.who != b.who:
                out[f"{a.speaker}->{b.speaker}"] += 1
            else:
                out["same"] += 1
        elif b:
            out[f"added:{b.speaker}"] += 1
        else:
            out[f"removed:{a.speaker}"] += 1
    return out


def _roles(turns) -> Counter:
    return Counter(t.speaker for t in turns)


def preview(shelf, cfg: Config, *, only: str | None = None) -> dict:
    """Per provider: records, how many are on the current version, how many sources can be compared, how many records would
    get a new edition, the role transitions and the role totals before and after. Read-only; counts only."""
    per: dict[str, dict] = defaultdict(lambda: {"records": 0, "on_current_version": 0, "compared": 0, "source_missing": 0,
                                                 "source_changed": 0, "unreadable": 0, "not_retained": 0,
                                                 "would_add_edition": 0, "unchanged_after": 0, "transitions": Counter(),
                                                 "roles_before": Counter(), "roles_after": Counter(),
                                                 "omitted_added": Counter(), "classes_after": Counter()})
    for entry in shelf.list_records():
        main = shelf.main_path(entry)
        try:
            meta, body = record.load(record.read(main))
            stored = render.parse_body(body)
        except (OSError, record.InvalidRecord):
            continue
        provider = entry.get("provider") or "unknown"
        if only and provider != only:
            continue
        row = per[provider]
        row["records"] += 1
        have = (meta.get("normalized") or {}).get("normalizer", sessions.DEFAULT_NORMALIZER)
        row["on_current_version"] += have >= current_version(provider)
        got, reason = fidelity.load_source(cfg, meta)
        if got is None:
            key = {fidelity.SOURCE_MISSING: "source_missing", fidelity.NOT_RETAINED: "not_retained"}.get(reason, "unreadable")
            row[key] += 1
            continue
        parsed, sha = got
        if sha != meta.get("source_hash"):
            row["source_changed"] += 1                       # cannot be compared fairly: the next capture is a new edition anyway
            continue
        clean, _ = render.scrub_parsed(parsed)
        row["compared"] += 1
        moves = align(stored, clean.turns)
        row["transitions"].update(k for k in moves.elements() if k != "same")
        row["roles_before"].update(_roles(stored))
        row["roles_after"].update(_roles(clean.turns))
        for t in clean.turns:
            if (t.attrs or {}).get("class"):
                row["classes_after"][t.attrs["class"]] += 1
        for kind, n in clean.omitted.items():
            if kind not in (meta.get("normalized", {}).get("omitted") or {}):
                row["omitted_added"][kind] += n
        changed = render.ordered_turns(stored) != render.ordered_turns(clean.turns)
        row["would_add_edition" if changed else "unchanged_after"] += 1
    return {"read_only": True, "providers": {p: {k: (dict(sorted(v.items())) if isinstance(v, Counter) else v)
                                                  for k, v in row.items()} for p, row in sorted(per.items())}}


def reprocess_inbox(shelf, cfg: Config, *, now: datetime | None = None) -> dict:
    """Re-read inbox records (claude.ai, Grok, pastes) whose parser version is older than the current one, from the file each
    keeps as its source, and add a new edition where the turns differ. Never deletes or rewrites an edition. The caller must
    have the owner's typed confirmation (the CLI asks); counts only."""
    from core.memory_shelf import inbox as inbox_mod
    now = now or datetime.now(timezone.utc)
    counts: Counter = Counter()
    for entry in list(shelf.list_records()):
        provider = entry.get("provider")
        if provider not in ("claude-ai", "grok"):
            continue
        main = shelf.main_path(entry)
        try:
            meta, _ = record.load(record.read(main))
        except (OSError, record.InvalidRecord):
            counts["unreadable"] += 1
            continue
        if (meta.get("normalized") or {}).get("normalizer", sessions.DEFAULT_NORMALIZER) >= current_version(provider):
            counts["already_current"] += 1
            continue
        ret = meta.get("retained") or {}
        path = Path(cfg.inbox_root) / ret.get("rel", "")
        if ret.get("kind") != "inbox-file" or not path.is_file():
            counts["source_missing"] += 1
            continue
        try:
            items = inbox_mod.load_items(path, datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc), only=ret.get("member"))
            item = next((i for i in items if i.member == ret.get("member")), None)
        except Exception:                                      # noqa: BLE001 - a fixed code only
            counts["unreadable"] += 1
            continue
        if item is None or item.skip or not item.parsed.turns or item.source_sha256 != meta.get("source_hash"):
            counts["source_changed" if item is not None and item.source_sha256 != meta.get("source_hash") else "unreadable"] += 1
            continue
        lkey = bundles.ledger_key_for("inbox", item.key)
        bundle = bundles.from_parsed(item.parsed, item.source_sha256, item.size, key=item.key, title=item.title, origin="inbox",
                                     created=item.created, retained=ret, ledger_key=lkey, kind=item.kind)
        result = shelf.ingest(bundle)
        counts[result.outcome] += 1
    return dict(sorted(counts.items()))


__all__ = ["preview", "reprocess_inbox", "align", "current_version"]
