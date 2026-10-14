"""The one search procedure (v0.5 "How search is organized"; amendment R5). M4's ``moi ask``, the CoS tool and
the brief all call ``search`` and get the same shape:

    {"query_id", "caller": {agent, entry_point}, "tier", "keyword": [hit ≤ k], "meaning": [hit ≤ k],
     "adjacent": {"none": ...}, "unresolved": [{chunk_id, list, reason}], "status": {...}}

* ``keyword``: FTS5, terms OR-ed, ranked by ``bm25()`` (never the ``rank`` column).
* ``meaning``: sqlite-vec nearest neighbours by cosine distance to the pinned embedder's query vector.
* Two labelled lists, never blended. One hit per (record, turn) per list. Empty is empty: nothing is padded.
* Every hit passes the resolver (``resolver.resolve``) before it is returned; failures land in ``unresolved``.
* The adjacent slot is filled only when the caller asks for it (``adjacent=True``, which Ask does): ``adjacent.py``.
* Optional ``created_after`` (inclusive) / ``created_before`` (exclusive), ``YYYY-MM-DD``, on the record's created date,
  for both lists and the adjacent slot; a malformed date is ``QueryRefused("bad_date")``.
* Refusals: no index → ``IndexMissing``; index from another schema/policy/chunker → ``IndexIncompatible``; the live
  embedder is not the pinned model or not the model the index was built with → ``QueryRefused("pin_mismatch")``.
  An unreachable embedder does not refuse the query: the keyword list is returned and the meaning list is empty
  with ``status.meaning = "embedder_unavailable"``.

This module never writes anything (the query log is M4's).
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import sqlite_vec

from core.memory_index import adjacent as adjacent_slot
from core.memory_index import indexer, policy, resolver, store
from core.memory_index.embedder import Embedder, EmbedderUnavailable, PinMismatch
from core.memory_shelf import ulid

DEFAULT_K = 5
OVERFETCH = 4                     # candidates per wanted hit, to absorb dedupe, scope filtering and unresolved hits
MAX_TERMS = 32
_TERM = re.compile(r"\w+", re.UNICODE)
WINDOW_REACH = 5                   # neighbours fetched per wanted one when a created window is set
ADJACENT_POOL = 600                # meaning neighbours the adjacent slot may choose from (measured on the real index: 60 are nearly
                                   # all from the records already answering, 600 cost ~150 ms and fill the slot)
ADJACENT_NOT_ASKED = {"none": "not_requested"}   # a plain index search leaves the slot to Ask (core/memory_ask)


class QueryRefused(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}{': ' + detail if detail else ''}")
        self.code = code


# Words that carry no topic in a conversational question. A natural sentence ("I'd like to know ... and how often") ORed
# word by word let these swamp the ranking: short chunks full of "I don't know" outranked the topic. They are dropped
# when anything else remains; a query made only of such words is searched as written. Fragments of contractions
# (don't -> don, t) are included because the tokenizer splits on the apostrophe.
STOPWORDS = frozenset("""
a about above after again all also am an and any are as at be because been before being below between both but by can could
d did didn do does doing don done down during each few for from further had hadn has hasn have haven having he her here hers
him his how i if in into is isn it its itself just know let like ll m me might more most much must my myself no nor not now
of off on once only or other our ours out over own re really s same shall she should shouldn so some such t than that the their
theirs them then there these they this those through to too under until up ve very want was wasn we were weren what when where
which while who whom why will with won would wouldn you your yours yourself
der die das und oder ist nicht ein eine ich du wir sie es mit von zu auf fuer im den dem des auch noch nur aber wie was wenn
""".split())
DF_CUTOFF = 0.15                  # a word in more than this share of all chunks says nothing about the topic either


def _terms(text: str) -> list[str]:
    return list(dict.fromkeys(t.lower() for t in _TERM.findall(text)))[:MAX_TERMS]


def _common(con, terms: list[str]) -> set[str]:
    """Terms found in more than ``DF_CUTOFF`` of the chunks (document frequency from the index's own vocabulary)."""
    try:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.fts_vocab USING fts5vocab(main, fts, 'row')")
        total = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] or 1
        marks = ",".join("?" * len(terms))
        return {t for t, n in con.execute(f"SELECT term, doc FROM temp.fts_vocab WHERE term IN ({marks})", terms)
                if n / total > DF_CUTOFF}
    except Exception:                                    # noqa: BLE001 - the vocabulary is an optimisation, never a failure
        return set()


def fts_query(text: str, con=None) -> str | None:
    terms = _terms(text)
    keep = [t for t in terms if t not in STOPWORDS] or terms
    if con is not None and len(keep) > 1:
        keep = [t for t in keep if t not in _common(con, keep)] or keep
    return " OR ".join('"' + t.replace('"', '') + '"' for t in keep) if keep else None


_COLS = ("id", "chunk_id", "record_id", "edition_hash", "turn", "speaker", "rel", "start_line", "end_line",
         "text_hash", "scope")


def _rows(con, sql: str, args: tuple) -> list[dict]:
    return [dict(zip(_COLS + ("score",), r)) for r in con.execute(sql, args).fetchall()]


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _window(after: str | None, before: str | None, col: str = "created") -> tuple[str, tuple]:
    """SQL for the optional created window: ``after`` inclusive, ``before`` exclusive, both ``YYYY-MM-DD``."""
    sql, args = "", []
    for value, op in ((after, ">="), (before, "<")):
        if value is None:
            continue
        if not _DATE.match(value):
            raise QueryRefused("bad_date", "created_after and created_before are YYYY-MM-DD")
        sql += f" AND {col} {op} ?"
        args.append(value)
    return sql, tuple(args)


def _keyword(con, text: str, caller: policy.Caller, n: int, window: tuple[str, tuple] = ("", ())) -> list[dict]:
    q = fts_query(text, con)
    if not q:
        return []
    marks = ",".join("?" * len(caller.tiers))
    sql = (f"SELECT {', '.join('c.' + c for c in _COLS)}, bm25(fts) AS score FROM fts JOIN chunks c ON c.id = fts.rowid "
           f"WHERE fts MATCH ? AND c.tier IN ({marks}) AND c.canary = ?{window[0].replace('created', 'c.created')} "
           f"ORDER BY score LIMIT ?")
    return _rows(con, sql, (q, *caller.tiers, int(caller.canary), *window[1], n))


def _meaning(con, vector: list[float], caller: policy.Caller, n: int, window: tuple[str, tuple] = ("", ())) -> list[dict]:
    marks = ",".join("?" * len(caller.tiers))
    near = con.execute(f"SELECT id, distance FROM vec WHERE embedding MATCH ? AND k = ? AND canary = ? "
                       f"AND tier IN ({marks})",
                       (sqlite_vec.serialize_float32(vector), n, int(caller.canary), *caller.tiers)).fetchall()
    if not near:
        return []
    dist = dict(near)
    ids = ",".join(str(int(i)) for i in dist)
    rows = _rows(con, f"SELECT {', '.join(_COLS)}, NULL FROM chunks WHERE id IN ({ids}){window[0]}", window[1])
    for r in rows:
        r["score"] = dist[r["id"]]
    return sorted(rows, key=lambda r: r["score"])


def _take(shelf_root: Path, rows: list[dict], caller: policy.Caller, k: int, label: str, unresolved: list,
          parsed: dict | None = None) -> list:
    hits, seen = [], set()
    for row in rows:
        if len(hits) >= k:
            break
        if caller.scopes is not None and row["scope"] not in caller.scopes:
            continue
        key = (row["record_id"], row["turn"])
        if key in seen:
            continue
        hit, bad = resolver.resolve(shelf_root, row, caller, label, parsed)
        if bad:
            unresolved.append(bad)
            continue
        seen.add(key)
        hits.append(hit)
    return hits


def search(query: str, *, shelf_root: str | Path, embedder: Embedder, index_path: str | Path | None = None,
           caller: policy.Caller | None = None, k: int = DEFAULT_K, adjacent: bool = False,
           created_after: str | None = None, created_before: str | None = None) -> dict:
    shelf_root = Path(shelf_root)
    index_path = Path(index_path) if index_path else indexer.default_index_path(shelf_root)
    caller = caller or policy.Caller()
    window = _window(created_after, created_before)          # refuses a malformed date before anything is opened
    reach = WINDOW_REACH if window[1] else 1                  # a window thins the neighbours, so look further for them
    started = time.monotonic()
    con = store.connect(index_path, readonly=True)
    try:
        if indexer.check_compatible(con, None):
            raise store.IndexIncompatible("index made by another schema, policy or chunker version")
        meta = store.get_meta(con)
        meaning_status, vector = "ok", None
        try:
            ident = embedder.check_pin()
            if meta.get("embedder_digest") and ident.digest != meta["embedder_digest"]:
                raise QueryRefused(indexer.PIN_MISMATCH, f"index built with {meta.get('embedder')}@"
                                   f"{meta['embedder_digest'][:12]}, embedder is {ident.label()}; run moi rebuild")
            vector = embedder.embed_query(query)
        except PinMismatch as exc:
            raise QueryRefused(indexer.PIN_MISMATCH, str(exc)) from None
        except EmbedderUnavailable:
            meaning_status = indexer.EMBEDDER_UNAVAILABLE
        finally:
            embedder.release()
        unresolved: list[dict] = []
        parsed: dict = {}                              # source-derived chunks, shared by both lists of this query
        keyword = _take(shelf_root, _keyword(con, query, caller, k * OVERFETCH, window), caller, k, "keyword", unresolved, parsed)
        pool = (_meaning(con, vector, caller, max(k * OVERFETCH, ADJACENT_POOL if adjacent else 0) * reach, window)
                if vector is not None else [])
        meaning = _take(shelf_root, pool[:k * OVERFETCH], caller, k, "meaning", unresolved, parsed) if vector is not None else []
        if not adjacent:
            slot = dict(ADJACENT_NOT_ASKED)
        elif vector is None:
            slot = {"none": "meaning_unavailable"}
        else:
            slot = adjacent_slot.pick(con, shelf_root, pool, keyword + meaning, caller, unresolved, parsed)
    finally:
        con.close()
    return {
        "query_id": ulid.new_ulid(), "caller": caller.label(), "tier": caller.tier, "canary": caller.canary,
        "window": {"created_after": created_after, "created_before": created_before},
        "keyword": keyword, "meaning": meaning, "adjacent": slot, "unresolved": unresolved,
        "status": {"meaning": meaning_status, "embedder": f"{meta.get('embedder')}@{str(meta.get('embedder_digest'))[:12]}",
                   "index_built_at": meta.get("built_at"), "index_updated_at": meta.get("updated_at"),
                   "ms": round((time.monotonic() - started) * 1000, 1)},
    }


__all__ = ["search", "QueryRefused", "fts_query", "DEFAULT_K"]
