"""F3, end to end (v0.5): the day's canary conversation must be *findable by Ask*, not merely present on the shelf.

The canary is a fixed synthetic conversation pushed through the real path (inbox -> normalizer -> shelf -> index). This
check asks the index for its fixed first sentence as the canary caller and looks for that day's canary record in the
answer: ``found`` (keyword and meaning both, or keyword only, said which), ``missing`` (the pipe is broken somewhere),
or ``unavailable`` (no index, or an index from another version). It uses the search procedure directly, so the check
never writes a line into the query log (the log is the evidence of what Robert and his agents ask, not of monitoring).
"""
from __future__ import annotations

from pathlib import Path

from core.memory_index import indexer, policy, search, store
from core.memory_shelf import canary, config
from core.memory_shelf.shelf import Shelf, read_front_matter

QUERY = canary.TURNS[0][1]


def _is_that_day(shelf: Shelf, hit: dict, day: str) -> bool:
    try:
        meta = read_front_matter(Path(shelf.root) / hit["file"])
    except (OSError, ValueError):
        return False
    return str(meta.get("source", "")).endswith(canary.canary_id(day))


def check(day: str, *, shelf_root, embedder, index_path=None) -> dict:
    shelf = Shelf(Path(shelf_root))
    caller = policy.Caller(tier="all", canary=True, agent="canary", entry_point="daily")
    try:
        result = search.search(QUERY, shelf_root=shelf_root, embedder=embedder, index_path=index_path, caller=caller, k=5)
    except (store.IndexMissing, store.IndexIncompatible):
        return {"state": "unavailable", "day": day}
    except search.QueryRefused as exc:
        return {"state": "unavailable", "day": day, "reason": exc.code}
    in_keyword = any(_is_that_day(shelf, h, day) for h in result["keyword"])
    in_meaning = any(_is_that_day(shelf, h, day) for h in result["meaning"])
    if not (in_keyword or in_meaning):
        return {"state": "missing", "day": day, "meaning": result["status"]["meaning"]}
    return {"state": "found", "day": day, "keyword": in_keyword, "meaning": in_meaning,
            "meaning_status": result["status"]["meaning"]}


__all__ = ["check", "QUERY"]
