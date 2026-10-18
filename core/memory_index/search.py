"""The one search procedure (v0.5 "How search is organized"; amendment R5). M4's ``moi ask``, the CoS tool and
the brief all call ``search`` and get the same shape:

    {"query_id", "caller": {agent, entry_point}, "tier", "keyword": [hit ≤ k], "meaning": [hit ≤ k],
     "adjacent": {"none": ...}, "unresolved": [{chunk_id, list, reason}], "status": {...}}

* ``keyword``: FTS5, terms OR-ed, ranked by ``bm25()`` (never the ``rank`` column).
* ``meaning``: sqlite-vec nearest neighbours by cosine distance to the pinned embedder's query vector.
* Two labelled lists, never blended. One hit per (record, turn) per list. Empty is empty: nothing is padded.
* Every hit passes the resolver (``resolver.resolve``) before it is returned; failures land in ``unresolved``.
* The adjacent slot is filled only when the caller asks for it (``adjacent=True``, which Ask does): ``adjacent.py``.
* Optional ``created_after`` (inclusive) / ``created_before`` (exclusive), ``YYYY-MM-DD``, on each chunk's time (its turn's
  own time where the source had one, else the record's created date), for both lists and the adjacent slot; a malformed
  date is ``QueryRefused("bad_date")``. A dated question with no topic gets the period overview (``_overview``).
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

try:
    import sqlite_vec
except ImportError:                         # keyword-only host: the meaning list reports vector_unavailable
    sqlite_vec = None

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


VECTOR_UNAVAILABLE = "vector_unavailable"
OVERVIEW = "period_overview"
# Words that only name WHEN (months in English/German, their short forms, period words): a dated question made of
# these and stop words has no topic, so keyword search would only look for the literal word ("may").
TEMPORAL = frozenset("""
january jan february feb march mar april apr may june jun july jul august aug september sep sept october oct november
nov december dec januar februar märz maerz mai juni juli oktober okt dezember dez month months monat monate week weeks
woche wochen year years jahr jahre period zeitraum
""".split()) | frozenset("""
work works worked working doing did done happen happened happening going go talk talked talking discuss discussed cover
covered busy up anything everything something stuff things thing overview summary summarize recap back then
gemacht machen gearbeitet arbeiten besprochen haben hatten passiert los
""".split())                      # activity words of an overview question ("what did we work on in May?")


def topic_terms(text: str) -> list[str]:
    """The words of a question that name a topic, not a time: neither stop words, temporal words nor years."""
    return [t for t in _terms(text) if t not in STOPWORDS and t not in TEMPORAL and not re.fullmatch(r"(19|20)\d\d", t)]


def _overview(con, caller: policy.Caller, n: int, after: str | None, before: str | None) -> tuple[list[dict], dict]:
    """For a dated question with no topic ("what were we doing in May?"): the conversations ACTIVE in the window (an
    indexed turn whose own time is inside it; ``last_activity >= after`` and ``first_activity < before`` narrow the records
    first, then a chunk in the window decides, so a conversation with turns only before and after the window is not
    counted), by provider, and for each one its first human passage from turns inside the window (else its first passage there),
    spread round-robin across providers with the conversations busiest in the window first, so the answer covers the
    period rather than one source. A conversation begun earlier and continued in the window is counted, and said so
    (``continued_from_before``). A record whose turns carry no times of their own is dated by its start alone
    (``dated_by_start_only``). Only records with indexed owner dialogue count (``chunks > 0``): an agent thread with no
    owner turn is not a conversation of the period and could never be shown."""
    marks = ",".join("?" * len(caller.tiers))
    act, aargs = "", []
    if after is not None:
        act += " AND r.last_activity >= ?"
        aargs.append(after)
    if before is not None:
        act += " AND r.first_activity < ?"
        aargs.append(before)
    cwin = _window(after, before, "c.created")
    # The caller's scopes bound the COUNTS as well as the passages: a restricted caller never learns how many
    # records it may not read exist, or under which provider (Codex r2 review).
    if caller.scopes is None:
        rscope = cscope = ("", ())
    elif not caller.scopes:
        rscope = cscope = (" AND 0", ())
    else:
        q = ",".join("?" * len(caller.scopes))
        rscope, cscope = (f" AND r.scope IN ({q})", tuple(caller.scopes)), (f" AND c.scope IN ({q})", tuple(caller.scopes))
    earlier = "r.first_activity < ?" if after is not None else "0"
    rows = con.execute(
        f"SELECT r.provider, count(*), coalesce(sum({earlier}), 0), coalesce(sum(r.timed_turns = 0), 0) FROM records r "
        f"WHERE r.state = 'indexed' AND r.chunks > 0 AND r.tier IN ({marks}) AND r.canary = ?{act}{rscope[0]} "
        f"AND EXISTS (SELECT 1 FROM chunks c WHERE c.record_id = r.record_id{cwin[0]}) GROUP BY r.provider",
        (*((after,) if after is not None else ()), *caller.tiers, int(caller.canary), *aargs, *rscope[1], *cwin[1])).fetchall()
    counts = {p: int(c) for p, c, _, _ in rows}
    firsts = con.execute(
        f"SELECT c.id, c.record_id, r.provider, c.speaker FROM chunks c JOIN records r ON r.record_id = c.record_id "
        f"WHERE r.state = 'indexed' AND c.tier IN ({marks}) AND c.canary = ?{act}{cwin[0]}{cscope[0]} "
        f"ORDER BY c.record_id, c.ordinal",
        (*caller.tiers, int(caller.canary), *aargs, *cwin[1], *cscope[1])).fetchall()
    best: dict[str, list] = {}
    for cid, record, provider, speaker in firsts:      # per record: its first human chunk in the window, else its first one
        held = best.get(record)
        if held is None:
            best[record] = [cid, provider, 1, speaker]
            continue
        held[2] += 1                                   # chunks in the window: the busier conversations come first
        if held[3] != "human" and speaker == "human":
            held[0], held[3] = cid, speaker
    by_provider: dict[str, list] = {}
    for cid, provider, _, _ in sorted(best.values(), key=lambda b: -b[2]):
        by_provider.setdefault(provider, []).append(cid)
    order, queues = [], [list(v) for _, v in sorted(by_provider.items())]
    while queues and len(order) < n:
        for q in queues:
            if q and len(order) < n:
                order.append(q.pop(0))
        queues = [q for q in queues if q]
    picked = _rows(con, f"SELECT {', '.join(_COLS)}, NULL FROM chunks WHERE id IN ({','.join(str(int(i)) for i in order) or 'NULL'})", ())
    rank = {rid: i for i, rid in enumerate(order)}
    picked.sort(key=lambda r: rank.get(r["id"], 0))
    return picked, {"records_in_window": sum(counts.values()), "by_provider": dict(sorted(counts.items())),
                    "continued_from_before": int(sum(r[2] for r in rows)),
                    "dated_by_start_only": int(sum(r[3] for r in rows))}


class _NoVectors(Exception):
    """This host serves keyword search only (no sqlite-vec)."""


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
            if sqlite_vec is None or store.is_keyword_only(con):   # no vectors here: never call an embedder for a list we cannot rank
                raise _NoVectors
            ident = embedder.check_pin()
            if meta.get("embedder_digest") and ident.digest != meta["embedder_digest"]:
                raise QueryRefused(indexer.PIN_MISMATCH, f"index built with {meta.get('embedder')}@"
                                   f"{meta['embedder_digest'][:12]}, embedder is {ident.label()}; run moi rebuild")
            vector = embedder.embed_query(query)
        except PinMismatch as exc:
            raise QueryRefused(indexer.PIN_MISMATCH, str(exc)) from None
        except EmbedderUnavailable:
            meaning_status = indexer.EMBEDDER_UNAVAILABLE
        except _NoVectors:
            meaning_status = VECTOR_UNAVAILABLE
        finally:
            embedder.release()
        unresolved: list[dict] = []
        parsed: dict = {}                              # source-derived chunks, shared by both lists of this query
        overview = None
        if window[1] and not topic_terms(query):
            pool, overview = _overview(con, caller, k * OVERFETCH, created_after, created_before)
            keyword = _take(shelf_root, pool, caller, k, "keyword", unresolved, parsed)
            vector = None                                   # an overview is not a meaning search
            meaning_status = OVERVIEW if meaning_status == "ok" else meaning_status
        else:
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
        **({"overview": overview} if overview is not None else {}),
        "status": {"meaning": meaning_status, "embedder": f"{meta.get('embedder')}@{str(meta.get('embedder_digest'))[:12]}",
                   "index_built_at": meta.get("built_at"), "index_updated_at": meta.get("updated_at"),
                   "ms": round((time.monotonic() - started) * 1000, 1)},
    }


__all__ = ["search", "QueryRefused", "fts_query", "DEFAULT_K"]
