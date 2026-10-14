"""The Ask procedure (v0.5 D1-D4): search + adjacent slot + one query-log line. Never writes anywhere else.

``ask`` returns the index's result (``query_id``, ``caller``, ``tier``, ``window``, the two labelled lists ``keyword``
and ``meaning``, the ``adjacent`` slot, ``unresolved``, ``status``) with ``status.log`` added (``ok`` or
``failed``: a log problem never costs the asker the answer, and is never silent). The caller identity comes in as a
``policy.Caller`` that the *host* built; Robert's CLI, CoS and MC all get identical results for identical queries.
"""
from __future__ import annotations

from pathlib import Path

from core.memory_ask import querylog
from core.memory_index import indexer, policy, search
from core.memory_index.embedder import Embedder

MAX_K = 5


class BadQuery(ValueError):
    pass


def ask(query: str, *, shelf_root, embedder: Embedder, caller: policy.Caller | None = None, k: int = search.DEFAULT_K,
        index_path=None, log_path=None, created_after: str | None = None, created_before: str | None = None,
        clock=None) -> dict:
    if not isinstance(query, str) or not query.strip():
        raise BadQuery("empty_query")
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= MAX_K:
        raise BadQuery("bad_k")
    shelf_root = Path(shelf_root)
    index_path = Path(index_path) if index_path else indexer.default_index_path(shelf_root)
    caller = caller or policy.Caller()
    result = search.search(query, shelf_root=shelf_root, embedder=embedder, index_path=index_path, caller=caller, k=k,
                           adjacent=True, created_after=created_after, created_before=created_before)
    try:
        querylog.record_query(Path(log_path) if log_path else querylog.path_for(index_path), query, result, clock=clock)
        result["status"]["log"] = "ok"
    except Exception:                                    # noqa: BLE001 - the answer stands; the failure is visible
        result["status"]["log"] = "failed"
    result["status"]["tiers"] = list(caller.tiers)
    return result


def opened(query_id: str, chunk_id: str, *, index_path, log_path=None, clock=None) -> None:
    querylog.record_opened(Path(log_path) if log_path else querylog.path_for(index_path), query_id, chunk_id, clock=clock)


def mark(query_id: str, value: str, *, index_path, log_path=None, clock=None) -> None:
    querylog.record_mark(Path(log_path) if log_path else querylog.path_for(index_path), query_id, value, clock=clock)


__all__ = ["ask", "opened", "mark", "BadQuery", "MAX_K"]
