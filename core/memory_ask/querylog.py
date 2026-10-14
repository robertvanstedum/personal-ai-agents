"""The query log (v0.5 D4): the evidence base for the six-month re-evaluation.

One append-only JSONL file beside the index (``<index folder>/ask-log.jsonl``, 0600). Three kinds of line, tied by
``query_id``: ``query`` (when, who asked, what, which hits came back), ``opened`` (which hit was opened) and ``mark``
(``useful`` or ``not``, the one-keystroke usefulness mark). Rules:

* **Passages are never logged**, only chunk ids (which name a record, an edition and an ordinal). The query text is the
  asker's own words, payment- and credential-scrubbed and capped before it is written.
* Each line is one ``write`` (``fsio.append_jsonl``), so concurrent askers never interleave.
* ``opened`` and ``mark`` are accepted only for a query that is in the log, and ``opened`` only for a chunk that query
  returned; anything else is refused, so the log cannot be filled with invented evidence.
* Every line names the caller (``agent``, ``entry_point``) as bound by the host: Robert's CLI, CoS and MC are told apart.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.agent_turns.writer import cap_and_scrub
from core.memory_shelf import fsio

NAME = "ask-log.jsonl"
VERSION = 1
MAX_QUERY = 2000
MARKS = ("useful", "not")


class LogRefused(ValueError):
    """An event for a query or hit the log does not know. ``code`` is fixed; the message never quotes anything."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def path_for(index_path) -> Path:
    return Path(index_path).parent / NAME


def _now(clock) -> str:
    return (clock() if clock else datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hit_ids(result: dict) -> dict:
    slot = result.get("adjacent") or {}
    return {"keyword": [h["chunk_id"] for h in result["keyword"]], "meaning": [h["chunk_id"] for h in result["meaning"]],
            "adjacent": slot.get("chunk_id")}


def record_query(path: Path, query: str, result: dict, *, clock=None) -> None:
    text, scrubbed = cap_and_scrub(query, MAX_QUERY)
    fsio.append_jsonl(path, {
        "v": VERSION, "event": "query", "at": _now(clock), "query_id": result["query_id"], "query": text,
        "sanitized": scrubbed, "caller": result["caller"], "tier": result["tier"], "window": result.get("window"),
        "hits": _hit_ids(result), "unresolved": len(result["unresolved"]),
        "status": {"meaning": result["status"]["meaning"], "ms": result["status"]["ms"]}})


def _lines(path: Path) -> list[dict]:
    try:
        return fsio.read_jsonl(path)
    except OSError:
        return []


def _query_row(path: Path, query_id: str) -> dict | None:
    return next((r for r in _lines(path) if r.get("event") == "query" and r.get("query_id") == query_id), None)


def record_opened(path: Path, query_id: str, chunk_id: str, *, clock=None) -> None:
    row = _query_row(path, query_id)
    if row is None:
        raise LogRefused("unknown_query")
    shown = [c for c in (row["hits"].get("keyword", []) + row["hits"].get("meaning", []) + [row["hits"].get("adjacent")]) if c]
    if chunk_id not in shown:
        raise LogRefused("not_a_hit_of_that_query")
    fsio.append_jsonl(path, {"v": VERSION, "event": "opened", "at": _now(clock), "query_id": query_id, "chunk_id": chunk_id,
                             "caller": row["caller"]})


def record_mark(path: Path, query_id: str, value: str, *, clock=None) -> None:
    if value not in MARKS:
        raise LogRefused("bad_mark")
    row = _query_row(path, query_id)
    if row is None:
        raise LogRefused("unknown_query")
    fsio.append_jsonl(path, {"v": VERSION, "event": "mark", "at": _now(clock), "query_id": query_id, "value": value,
                             "caller": row["caller"]})


def summary(path: Path) -> dict:
    """Counts only: queries by agent and entry point, hits opened, marks, queries with an empty first list."""
    out = {"queries": 0, "by_caller": {}, "opened": 0, "marks": {m: 0 for m in MARKS}, "no_keyword_hits": 0,
           "no_meaning_hits": 0, "adjacent_returned": 0}
    for row in _lines(path):
        event = row.get("event")
        if event == "query":
            out["queries"] += 1
            who = f"{row['caller']['agent']}/{row['caller']['entry_point']}"
            out["by_caller"][who] = out["by_caller"].get(who, 0) + 1
            out["no_keyword_hits"] += not row["hits"]["keyword"]
            out["no_meaning_hits"] += not row["hits"]["meaning"]
            out["adjacent_returned"] += bool(row["hits"].get("adjacent"))
        elif event == "opened":
            out["opened"] += 1
        elif event == "mark" and row.get("value") in MARKS:
            out["marks"][row["value"]] += 1
    return out


__all__ = ["NAME", "MARKS", "LogRefused", "path_for", "record_query", "record_opened", "record_mark", "summary"]
