"""Doctor and repair for the shelf's main record files (amendment §10 follow-up, Codex review 2026-10-04).

A record's **editions** are its immutable history; its main file is a derived view (front matter plus the turns
of the current edition). ``inspect`` compares each main file with its own editions, **read-only**, and reports
**counts and short ids only**: never a turn, a title or a file name.

States, per record:

* ``ok``: the body reads back and holds exactly the current edition's turns.
* ``body_unreadable``: the body no longer matches its own turn frames (the old writer trimmed or translated bytes).
* ``edition_mismatch``: the body reads back but its turns differ from the current edition's.
* ``main_behind``: a newer edition was saved but the main file never moved to it (an interrupted publication). The
  next watcher pass finishes it; nothing to repair by hand.
* ``edition_missing`` / ``edition_corrupt`` / ``main_unreadable``: nothing trustworthy to rebuild from; reported, never touched.

``recover`` rebuilds the body of a ``body_unreadable`` or ``edition_mismatch`` record **from its current edition**,
after copying the old main file into ``<record>/repairs/``. Editions are never rewritten or removed. The rebuilt body
has the turns only (the edition does not keep the omitted-part pointers): one header line says so and carries the
omitted counts from the edition.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core.memory_shelf import editions, fidelity, fsio, record, render
from core.memory_shelf.sessions import Parsed

OK, BODY_UNREADABLE, EDITION_MISMATCH, MAIN_BEHIND = "ok", "body_unreadable", "edition_mismatch", "main_behind"
EDITION_MISSING, EDITION_CORRUPT, MAIN_UNREADABLE = "edition_missing", "edition_corrupt", "main_unreadable"
RECOVERABLE = frozenset({BODY_UNREADABLE, EDITION_MISMATCH})


def classify(main: Path) -> str:
    try:
        meta, body = record.load(record.read(main))
    except (OSError, ValueError, record.InvalidRecord):
        return MAIN_UNREADABLE
    folder = main.parent
    current = int(meta.get("edition") or 1)
    try:
        on_edition = fidelity.edition_turns(folder, current)
    except (OSError, ValueError, KeyError):
        return EDITION_CORRUPT
    if on_edition is None:
        return EDITION_MISSING
    if any(row[0] == -1 for row in on_edition):
        return EDITION_CORRUPT
    try:
        in_body = render.ordered_turns(render.parse_body(body))
    except record.InvalidRecord:
        return BODY_UNREADABLE
    if in_body != on_edition:
        return EDITION_MISMATCH
    newest = max((e.number for e in editions.list_editions(folder)), default=current)
    return MAIN_BEHIND if newest > current else OK


def inspect(shelf, *, canary: bool = False) -> dict:
    """Read-only. ``{"records": n, "states": {state: count}, "ids": {state: [short ids]}}`` for non-ok states."""
    from core.memory_shelf import ulid
    states: dict[str, int] = {}
    ids: dict[str, list[str]] = {}
    entries = shelf.list_records(canary=canary)
    for entry in entries:
        state = classify(shelf.main_path(entry))
        states[state] = states.get(state, 0) + 1
        if state != OK:
            ids.setdefault(state, []).append(ulid.short(entry["id"]))
    return {"records": len(entries), "states": dict(sorted(states.items())), "ids": {k: sorted(v) for k, v in sorted(ids.items())}}


def _edition_rows(folder: Path, number: int) -> tuple[dict, list[dict]]:
    for ed in editions.list_editions(folder):
        if ed.number == number:
            with open(ed.path, "rb") as handle:
                head = json.loads(handle.readline())
                return head, [json.loads(line) for line in handle]
    raise FileNotFoundError


def recover(shelf, record_id: str, *, now: datetime | None = None) -> str:
    """Rebuild one record's body from its current edition. Returns ``repaired`` or the state that was found."""
    path = shelf.find_record(record_id)
    if path is None or not path.is_file():
        return "unknown_id"
    with shelf.lock():
        state = classify(path)
        if state not in RECOVERABLE:
            return state
        meta, _ = record.load(record.read(path))
        head, rows = _edition_rows(path.parent, int(meta.get("edition") or 1))
        rebuilt = Parsed(str(head.get("provider") or "unknown"), str(meta.get("chair") or "unknown"))
        omitted = ", ".join(f"{k}={v}" for k, v in sorted((head.get("omitted") or {}).items()))
        rebuilt.mark(f"[recovered from edition {meta.get('edition')}: turns only; omitted-part pointers were not kept"
                     + (f" (omitted: {omitted})" if omitted else "") + "]")
        for row in rows:
            rebuilt.add(row["speaker"], row["text"], row["line"], row["basis"])
        body = render.render_body(rebuilt)
        if render.ordered_turns(render.parse_body(body)) != fidelity.edition_turns(path.parent, int(meta.get("edition") or 1)):
            return "rebuild_mismatch"                          # never write a body that is not the edition's
        stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
        keep = path.parent / "repairs"
        fsio.ensure_dir(keep)
        fsio.write_atomic(keep / f"{stamp}-main.md", path.read_bytes())
        record.write(path, meta, body)
        return "repaired"


__all__ = ["inspect", "recover", "classify", "RECOVERABLE"]
